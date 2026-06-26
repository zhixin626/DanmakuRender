import logging
import os
import re
import time
from pathlib import Path
from datetime import datetime

from DMR.LiveAPI import LiveAPI
from DMR.utils.bark_notifier import bark_notify_url
from .baseevents import BaseEvents
from ..utils import *
from ..utils.utils import format_duration
from ..utils.gifts_utils import generate_gift_statistics
from ..utils.session_record import write_record, delete_record, delete_all_records, read_last_complete_session

logger = logging.getLogger(__name__)

# 会话级"格式化标本"字段：不放进 VideoInfo，上传时作为单独的 session dict 传给 uploader 格式化
SESSION_FIELDS = ('stime', 'etime', 'totaltime',
                  'gifts_revenue', 'num_of_gifters', 'sc_revenue', 'num_of_sc',
                  'member_revenue', 'member_info', 'total_revenue', 'top_ranking')

class LiveEvents(BaseEvents): # 被 class ReplayTask()初始化
    def __init__(self, name, config):
        super().__init__(name, config)
        self.state_dict = {}
        self.ended_dict = {}
        self.session_data = {}
        self.merged_videos = []  # 合并产物记录（供 WebUI 展示，保留最近若干条）
        self.held_tasks = {}     # 挂起待人工处理的任务：hold_id -> 详情（仅内存，重启即失）
        self.logger = logging.getLogger(__name__)
        # 任务级常量路径：output_dir 整个任务不变，构造时算一次（不随每场直播变化）
        out_dir = str(self.config['download_args']['output_dir'])
        self.src_path       = Path(out_dir)
        self.dmvideo_path   = Path(out_dir + '（弹幕版）')
        self.transcode_path = Path(out_dir + '（转码后）')

    def _persist_record(self, group_id):
        """把该 group 的内存状态忠实镜像到 output_dir 的会话记录。吞异常，不影响主管线。"""
        try:
            if group_id is None or group_id not in self.state_dict:
                return
            out_dir = str(self.config['download_args']['output_dir'])
            write_record(out_dir, group_id, self.name,
                         self.state_dict.get(group_id),
                         self.session_data.get(group_id))
        except Exception as e:
            self.logger.debug(f'写会话记录失败(已忽略): {e}')

    def _delete_record(self, group_id):
        """group 全部处理完、内存释放时，删除其会话记录文件。吞异常，不影响主流程。"""
        out_dir = str(self.config['download_args']['output_dir'])
        delete_record(out_dir, group_id)

    @property
    def event_dict(self):
        # 事件名(source/event) -> 处理函数；由上层 ReplayTask 分发
        return {
            'ready': self.onReady,
            'exit': self.onExit,
            'downloader/livestart': self.onLiveStart,
            'downloader/livesegment': self.onLiveSegment,
            'downloader/liveend': self.onLiveEnd,
            'downloader/livestop': self.onLiveEnd,
            'render/end': self.onRenderEnd,
            'render/error': self.onRenderError,
            'uploader/end': self.onUploadEnd,
            'uploader/error': self.onUploadError,
            'cleaner/end': self.onCleanEnd,
            'cleaner/error': self.defaultEvent,
            'merger/end': self.onMergeEnd,
            'merger/error': self.onMergeError,
            'webservice/resume': self.onResume,   # 人工处理完，喂回结果让管线继续
            'default': self.defaultEvent,
        }

    def defaultEvent(self, message:PipeMessage):  # event_dict 里没匹配上的事件走这里
        self.logger.info(f'{self.name}: {message.msg}')

    def onLiveStart(self, message:PipeMessage):
        group_id = message.data
        # 开播时只知道 is_live_end；会话字段(stime/gifts/...)由 onLiveEnd 的 _collect_session_data 填。
        # is_live_end 也是 engine 空闲检测的依据，必须显式置 False。
        self.session_data[group_id] = {
            "is_live_end": False,
        }
        self.bark_notify(message.url)


    def bark_notify(self, url):
        bark_args = self.config.get("bark_args", {})
        if not bark_args.get("is_bark"):
            return
        bark_notify_url(url, sound=bark_args.get("sound", "birdsong"))

    def onReady(self, *args, **kwargs):
        return PipeMessage(
            source=self.name,
            target='downloader',
            event='newtask',
            data={
                'dltype': self.config['download_args']['dltype'],
                'taskname': self.name,
                'config': self.config['download_args'],
            }
        )
    
    def onLiveSegment(self, message:PipeMessage):
        self.logger.info(f'{self.name}: {message.msg}')
        video:VideoInfo = message.data
        video_state = {
            'src_video': {'status': None, 'file': None, 'wait': []},
            'src_video_pre': {'status': None, 'file': None, 'wait': []},
            'dm_video': {'status': None, 'file': None, 'wait': []},
        }
        if self.state_dict.get(video.group_id):
            self.state_dict[video.group_id].append(video_state)
        else:
            self.state_dict[video.group_id] = [video_state]

        # 录到的源 flv 一律登记到 src_video_pre（方便后续上传/清理）
        self.state_dict[video.group_id][-1]['src_video_pre'].update({'status': 'ready', 'file': video})

        ret_msgs = []
        ret_msgs += self._check_for_transcode(video)   # 新段的渲染/转码按段派发
        ret_msgs += self._check_for_render(video)
        ret_msgs += self.reconcile(video.group_id)     # 其余（实时上传/持久化）统一交给 reconcile
        return ret_msgs

    def _check_for_transcode(self, video):
        """转码派发：auto_transcode 开则给原视频发转码任务（src_video 标 rendering）。返回消息列表。"""
        ret_msgs = []
        if not self.config['common_event_args'].get('auto_transcode'):
            return ret_msgs
        transcode_args = self.config['render_args']['transcode']
        if transcode_args.get('output_name'):
            filename = replace_keywords(transcode_args['output_name'], video, replace_invalid=True) + \
                    f".{transcode_args.get('format','mp4')}"
        else:
            filename = os.path.splitext(os.path.basename(video.path))[0] + \
                    f"（转码后）.{transcode_args.get('format','mp4')}"
        output_dir = transcode_args.get('output_dir') or (os.path.dirname(video.path) + '（转码后）')
        output = os.path.join(output_dir, filename)
        transcode_msg = PipeMessage(
            source=self.name, target='render', event='newtask', request_id=uuid(),
            data={'taskname': self.name, 'mode': 'transcode', 'video': video,
                  'output': output, 'args': transcode_args})
        slot = self.state_dict[video.group_id][-1]['src_video']
        slot['status'] = 'rendering'
        slot['wait'].append(transcode_msg.request_id)
        ret_msgs.append(transcode_msg)
        return ret_msgs

    def _check_for_render(self, video):
        """渲染派发：auto_render 开则给该段发弹幕渲染任务（dm_video 标 rendering）。返回消息列表。"""
        ret_msgs = []
        if not self.config['common_event_args'].get('auto_render'):
            return ret_msgs
        render_args = self.config['render_args']['dmrender']
        # 配置 dmrender.emoji=true 时改用彩色 emoji 渲染引擎
        render_mode = 'emoji_dmrender' if render_args.get('emoji') else 'dmrender'
        if render_args.get('output_name'):
            filename = replace_keywords(render_args['output_name'], video, replace_invalid=True) + \
                    f".{render_args.get('format','mp4')}"
        else:
            filename = os.path.splitext(os.path.basename(video.path))[0] + \
                    f"（弹幕版）.{render_args.get('format','mp4')}"
        output_dir = render_args.get('output_dir') or (os.path.dirname(video.path) + '（弹幕版）')
        output = os.path.join(output_dir, filename)
        render_msg = PipeMessage(
            source=self.name, target='render', event='newtask', request_id=uuid(),
            data={'taskname': self.name, 'mode': render_mode, 'video': video,
                  'output': output, 'args': render_args})
        slot = self.state_dict[video.group_id][-1]['dm_video']
        slot['status'] = 'rendering'
        slot['wait'].append(render_msg.request_id)
        ret_msgs.append(render_msg)
        return ret_msgs

    def onLiveEnd(self, message:PipeMessage):
        group_id = message.data
        if group_id is None:
            return

        self.session_data[group_id]['is_live_end'] = True

        if group_id in self.state_dict:
            self.ended_dict[group_id] = time.time()
        else:
            self.logger.debug(f'No such group:{group_id}.')

        self._collect_session_data(group_id)      # 收集礼物/时长到 session_data
        return self.reconcile(group_id, clean_trigger='onLiveEnd')

    def reconcile(self, group_id, clean_trigger=None):
        """状态推进收口：任何事件改完状态后调它，统一决定该派发的合并/上传/清理，再持久化 + 回收。
        各 _check_* 都幂等（只对够格的段派发并立刻改状态防重复），故每次统一重跑也安全。
        clean_trigger 指明本轮清理阶段（onRenderEnd/onUploadEnd/onLiveEnd/onMergeEnd）；None=本轮不触发清理。
        注意：合并失败回滚走 onMergeError、不经此处，避免回滚后又被立刻重新派发合并。"""
        ret_msgs = []
        ret_msgs += self._check_for_merge(group_id)
        ret_msgs += self._check_for_upload(group_id)
        if clean_trigger:
            ret_msgs += self._check_for_clean(group_id, trigger=clean_trigger)
        # 补查 onLiveEnd 清理：兜住「下播后才就绪」的段（典型是最后一段，渲染慢于下播）。
        # 这种段就绪那拍带的是 onRenderEnd 等事件，与默认 onLiveEnd 触发器对不上会被跳过；
        # 这里趁本组已下播，用对的 onLiveEnd 触发器再跑一次把它接住。clean_trigger 本就是
        # onLiveEnd 时（下播事件那拍）不必重复跑。
        if group_id in self.ended_dict and clean_trigger != 'onLiveEnd':
            ret_msgs += self._check_for_clean(group_id, trigger='onLiveEnd')
        self._persist_record(group_id)
        self._free_state_memory()
        return ret_msgs

    def _check_for_upload(self, group_id:str):
        """上传总入口：门控 + 共享上下文，分发给「实时上传」和「结束上传」两条路径。"""
        ret_msgs = []
        if not self.config['common_event_args'].get('auto_upload'):
            return ret_msgs
        if not self.state_dict.get(group_id):
            return ret_msgs

        upload_args = self.config['upload_args']
        # 会话级"格式化标本"：随上传消息传给 uploader 去 format（title/desc/dynamic）；
        # 其中 session['stime'] 也是 biliwebapi 周期追加的 period key 依据。
        session = {k: (self.session_data.get(group_id) or {}).get(k, '') for k in SESSION_FIELDS}

        ret_msgs += self._check_for_realtime_upload(group_id, upload_args, session)
        ret_msgs += self._check_for_final_upload(group_id, upload_args, session)
        return ret_msgs

    def _check_for_realtime_upload(self, group_id, upload_args, session):
        """实时上传：录制中，分段一就绪就上传（仅 arg.realtime=True 的上传目标）。"""
        ret_msgs = []
        for idx, video_state in enumerate(self.state_dict[group_id]):
            for vtype, info in video_state.items():
                if info['status'] != 'ready':
                    continue
                for upload_file_types, upload_arg in upload_args.items():
                    if vtype not in upload_file_types.split('+'):
                        continue
                    for upid, arg in enumerate(upload_arg):
                        if not arg.get('realtime'):
                            continue
                        if info['file'].duration < arg.get('min_length', 0):
                            continue
                        upload_group_id = info['file'].upload_group_id if hasattr(info['file'], 'upload_group_id') else group_id
                        upload_msg = PipeMessage(
                            source=self.name, target='uploader', event='newtask', request_id=uuid(),
                            data={
                                'taskname': self.name,
                                'files': [info['file']],
                                'engine': arg['engine'],
                                'stateless': False,
                                'upload_group': upload_group_id+'_'+upload_file_types+'_'+str(upid),
                                'session': session,
                                'output_dir': str(self.src_path),
                                'args': arg,
                            })
                        self.state_dict[group_id][idx][vtype]['status'] = 'uploading'
                        self.state_dict[group_id][idx][vtype]['wait'].append(upload_msg.request_id)
                        ret_msgs.append(upload_msg)
        return ret_msgs

    def _check_for_final_upload(self, group_id, upload_args, session):
        """结束上传：group 录制结束后，把就绪的分段批量上传（仅非 realtime 的上传目标）。"""
        ret_msgs = []
        if group_id not in self.ended_dict:
            return ret_msgs
        video_types = list(self.state_dict[group_id][-1].keys())
        for vtype in video_types:
            # 该 vtype 的所有分段是否都就绪（merged/None 跳过，配合 _check_for_merge）
            videos = []
            for video_state in self.state_dict[group_id]:
                st = video_state[vtype]['status']
                if st == 'ready':
                    videos.append(video_state[vtype]['file'])
                elif st in ('merged', 'cleaned', 'cleaning', None):
                    continue   # 已了结、不参与本次上传：合并源(merged/已删源 cleaned/清理中)、未参与(None)
                else:
                    videos = []   # rendering/uploading 等真在途 → 还没都就绪，放弃这轮、等齐再传
                    break
            if not videos:
                continue

            for upload_file_types, upload_arg in upload_args.items():
                if vtype not in upload_file_types.split('+'):
                    continue
                for upid, arg in enumerate(upload_arg):
                    if arg.get('realtime'):
                        continue
                    up_videos = [v for v in videos if v.duration >= arg.get('min_length', 0)]
                    if not up_videos:
                        self.logger.info(f'{self.name}: 视频时长不足 {arg.get("min_length", 0)} 秒，跳过上传。')
                        continue
                    upload_group_id = up_videos[0].upload_group_id if hasattr(up_videos[0], 'upload_group_id') else group_id
                    upload_msg = PipeMessage(
                        source=self.name, target='uploader', event='newtask', request_id=uuid(),
                        data={
                            'taskname': self.name,
                            'files': up_videos,
                            'engine': arg['engine'],
                            'stateless': True,
                            'upload_group': upload_group_id+'_'+upload_file_types+'_'+str(upid),
                            'session': session,
                            'output_dir': str(self.src_path),
                            'args': arg,
                        })
                    # 只标"真正进了这次上传"的分段；merged/时长不足/None 保持原状
                    up_ids = set(id(v) for v in up_videos)
                    for video_state in self.state_dict[group_id]:
                        slot = video_state[vtype]
                        if slot['file'] is not None and id(slot['file']) in up_ids:
                            slot['status'] = 'uploading'
                            slot['wait'].append(upload_msg.request_id)
                    ret_msgs.append(upload_msg)
        return ret_msgs
    
    def onRenderEnd(self, message:PipeMessage):
        self.logger.info(f'{self.name}: {message.msg}.')

        request_id = message.request_id
        video:VideoInfo = message.data.get('output')
        video_states = self.state_dict[video.group_id]
        # 按 request_id 移除对应的等待；wait 清空则该段标 ready 并挂上渲染产物
        for idx, video_state in enumerate(video_states):
            for vtype, info in video_state.items():
                if request_id in info['wait']:
                    self.state_dict[video.group_id][idx][vtype]['wait'].remove(request_id)
                    if len(self.state_dict[video.group_id][idx][vtype]['wait']) == 0:
                        self.state_dict[video.group_id][idx][vtype]['status'] = 'ready'
                        self.state_dict[video.group_id][idx][vtype]['file'] = video

        return self.reconcile(video.group_id, clean_trigger='onRenderEnd')

    def onUploadEnd(self, message:PipeMessage):
        self.logger.info(f'{self.name}: {message.msg}.')

        target_group_id = None
        request_id = message.request_id
        # 按 request_id 移除等待；wait 清空则该段标 uploaded
        for group_id, video_states in self.state_dict.items():
            for idx, video_state in enumerate(video_states):
                for vtype, info in video_state.items():
                    if request_id in info['wait']:
                        target_group_id = group_id
                        self.state_dict[group_id][idx][vtype]['wait'].remove(request_id)
                        if len(self.state_dict[group_id][idx][vtype]['wait']) == 0:
                            self.state_dict[group_id][idx][vtype]['status'] = 'uploaded'

        # bvid 历史的读/写、是否追加，均由 biliwebapi 在 upload() 内自行处理（见 bvid_history）
        return self.reconcile(target_group_id, clean_trigger='onUploadEnd')

    def _check_for_merge(self, group_id):
        """异步合并派发：检查每个 merge_type 是否齐活（全 ready/uploaded），齐则把这些分段
        标 merging + 挂 request_id，并发一个 merge job 给 Merger 插件。实际合并/重命名/probe/
        建 VideoInfo 由 Merger 执行，完成后回 onMergeEnd / onMergeError。
        返回要发出的 merge job 消息列表（调用方需并入 ret_msgs 一起发送）。"""
        merge_cfg  = self.config.get("merge_args", {}) or {}
        merge_type = merge_cfg.get("merge_type")
        auto_merge = self.config['common_event_args'].get('auto_merge')

        # 基础防御：未开启合并 / 没有这个 group / merge_type 为空 → 直接退出
        if (not auto_merge) or (group_id not in self.ended_dict) or (not merge_type):
            return []

        stime, _ = read_last_complete_session(self.src_path)   # stime 仅用于合并文件名模板
        target_slot = {'src_video': 'src_video', 'dm_video': 'dm_video'}

        ret_msgs = []
        for vt in merge_type:
            # 检查该 vt 所有分段是否都 ready/uploaded（None=未参与，跳过；其余状态=还不能合）
            entries = []
            ok = True
            for vs in self.state_dict[group_id]:
                entry = vs.get(vt)
                if entry is None:
                    ok = False
                    break
                if entry['status'] is None:
                    continue
                if entry['status'] not in ('ready', 'uploaded'):
                    ok = False
                    break
                entries.append(entry)
            if not ok or not entries:
                continue

            # 标 merging + 挂 request_id（存合并前状态，供 onMergeError 回滚）
            req = uuid()
            paths = [e['file'].path for e in entries]
            tail = entries[-1]['file']
            dst = target_slot.get(vt, vt)
            new_seg_id = len(self.state_dict[group_id]) + 1
            for e in entries:
                e['_pre_merge_status'] = e['status']
                e['status'] = 'merging'
                e['wait'].append(req)

            merge_msg = PipeMessage(
                source=self.name, target='merger', event='newtask', request_id=req,
                data={
                    'taskname'       : self.name,
                    'paths'          : paths,
                    'merge_type'     : vt,
                    'args'           : merge_cfg,   # 整个 merge_args 交给 merger（仿 uploader 的 'args'）
                    'new_video_meta' : {
                        'dtype'     : dst, 'stime': stime,   # stime 仅用于文件名模板
                        'segment_id': new_seg_id, 'taskname': tail.taskname,
                        'group_id'  : group_id, 'streamer': tail.streamer, 'title': tail.title,
                    },
                })
            ret_msgs.append(merge_msg)
        return ret_msgs

    def onMergeEnd(self, message: PipeMessage):
        """Merger 回报合并完成：把被合并段按 remove_source 标状态、把合并结果作为新段 ready 追加，
        再回填会话数据并交给 reconcile 推进上传/清理。
        被合并的源段：merger 自己删了源(remove_source 默认 True)→ 标 cleaned(已无文件、终态)；
        没删(remove_source False)→ 标 merged(留着，等 onMergeEnd 的 trigger 清，且 merged 不会被重合并)。
        合并产物(ready)与源段一样受 dm_video 的 trigger 清理，不做产物/源区分（见 _check_for_clean）。"""
        request_id = message.request_id
        data = message.data or {}
        merged_video = data.get('output')
        vt = data.get('merge_type')
        if merged_video is None:
            return []
        group_id = merged_video.group_id
        if group_id not in self.state_dict:
            return []

        # 1. 被合并的段：移除 request_id；merger 已删源→cleaned，未删→merged（防重合并、待清理）
        remove_source = (self.config.get('merge_args') or {}).get('remove_source', True)
        merged_status = 'cleaned' if remove_source else 'merged'
        n_merged = 0
        for vs in self.state_dict[group_id]:
            slot = vs.get(vt)
            if slot and request_id in slot['wait']:
                slot['wait'].remove(request_id)
                slot['status'] = merged_status
                slot.pop('_pre_merge_status', None)
                n_merged += 1

        # 2. 合并结果作为新段追加（ready）
        new_state = {
            'src_video':     {'status': None, 'file': None, 'wait': []},
            'src_video_pre': {'status': None, 'file': None, 'wait': []},
            'dm_video':      {'status': None, 'file': None, 'wait': []},
        }
        merged_video.segment_id = len(self.state_dict[group_id]) + 1
        new_state.setdefault(vt, {'status': None, 'file': None, 'wait': []})
        new_state[vt] = {'status': 'ready', 'file': merged_video, 'wait': []}
        self.state_dict[group_id].append(new_state)

        # 3. 记录合并产物（供 WebUI「已合并的视频」展示）
        self.merged_videos.append({'output': str(merged_video.path), 'type': vt,
                                   'segments': n_merged, 'time': datetime.now()})
        if len(self.merged_videos) > 200:
            self.merged_videos = self.merged_videos[-200:]

        # 4. 交给 reconcile：先看上传，再以 onMergeEnd trigger 清理（源段 + 合并产物一并）
        return self.reconcile(group_id, clean_trigger='onMergeEnd')

    # ---- 失败挂起：每步重试用尽后转人工。挂起的段标 'holding'，登记到 held_tasks 供 WebUI ----
    def _locate_by_request(self, request_id):
        """按 request_id 找到唯一一个挂着它的槽 (group_id, seg_idx, vtype)；找不到返回 None。"""
        for gid, vss in self.state_dict.items():
            for idx, vs in enumerate(vss):
                for vt, slot in vs.items():
                    if request_id in slot.get('wait', []):
                        return gid, idx, vt
        return None

    def _locate_all_by_request(self, request_id):
        """按 request_id 找到所有挂着它的槽（合并/批量上传会涉及多段）。"""
        out = []
        for gid, vss in self.state_dict.items():
            for idx, vs in enumerate(vss):
                for vt, slot in vs.items():
                    if request_id in slot.get('wait', []):
                        out.append((gid, idx, vt))
        return out

    def _add_hold(self, stage, group_id, segs, vtype, info):
        """登记一个挂起任务，返回 hold_id。info 是给人看的内容（各阶段不同）。"""
        hid = uuid()
        self.held_tasks[hid] = {
            'hold_id': hid, 'stage': stage, 'group_id': group_id,
            'segs': list(segs), 'vtype': vtype, 'info': info, 'time': datetime.now(),
        }
        self.logger.warning(f'{self.name}: {stage} 失败，已挂起等待人工处理 (hold_id={hid})')
        return hid

    def _build_videoinfo_from(self, src, path, dtype):
        """以源 VideoInfo 为模板、替换成人工产出的文件，构造一个新的 VideoInfo。"""
        base = dict(src) if src else {}
        base.update({'path': path, 'dtype': dtype, 'file_id': uuid(),
                     'ctime': datetime.now(), 'dm_file_id': None})
        try:
            base['size'] = os.path.getsize(path)
        except Exception:
            base['size'] = None
        return VideoInfo(**base)

    def onRenderError(self, message:PipeMessage):
        """渲染失败转人工：挂起此段，详情交 WebUI。"""
        self.logger.info(f'{self.name}: {message.msg}')
        loc = self._locate_by_request(message.request_id)
        if not loc:
            return []
        group_id, seg_idx, vtype = loc
        slot = self.state_dict[group_id][seg_idx][vtype]
        slot['wait'] = [r for r in slot['wait'] if r != message.request_id]
        slot['status'] = 'holding'
        src = (self.state_dict[group_id][seg_idx].get('src_video_pre') or {}).get('file')
        info = {
            'flv'  : getattr(src, 'path', None),
            'ass'  : getattr(src, 'dm_file_id', None),
            'vtype': vtype,
            'error': str(message.data if message.data is not None else message.msg),
        }
        self._add_hold('render', group_id, [seg_idx], vtype, info)
        return self.reconcile(group_id, clean_trigger=None)

    def onMergeError(self, message:PipeMessage):
        """合并失败转人工：挂起参与的段，详情交 WebUI。"""
        self.logger.info(f'{self.name}: {message.msg}')
        locs = self._locate_all_by_request(message.request_id)
        if not locs:
            return []
        group_id, _, vtype = locs[0]
        seg_idxs, inputs = [], []
        for gid, idx, vt in locs:
            slot = self.state_dict[gid][idx][vt]
            slot['wait'] = [r for r in slot['wait'] if r != message.request_id]
            slot['status'] = 'holding'
            slot.pop('_pre_merge_status', None)
            seg_idxs.append(idx)
            if slot.get('file'):
                inputs.append(slot['file'].path)
        info = {
            'merge_type': vtype,
            'inputs'    : inputs,
            'error'     : str((message.data or {}).get('error') if isinstance(message.data, dict) else message.msg),
        }
        self._add_hold('merge', group_id, seg_idxs, vtype, info)
        return self.reconcile(group_id, clean_trigger=None)

    def onUploadError(self, message:PipeMessage):
        """上传失败转人工：挂起此段，详情交 WebUI。"""
        self.logger.info(f'{self.name}: {message.msg}')
        locs = self._locate_all_by_request(message.request_id)
        if not locs:
            return []
        group_id, _, vtype = locs[0]
        seg_idxs, files = [], []
        for gid, idx, vt in locs:
            slot = self.state_dict[gid][idx][vt]
            slot['wait'] = [r for r in slot['wait'] if r != message.request_id]
            slot['status'] = 'holding'
            seg_idxs.append(idx)
            if slot.get('file'):
                files.append(slot['file'])
        cfg = (message.data or {}).get('config', {}) if isinstance(message.data, dict) else {}
        extra = (message.data or {}).get('extra', {}) if isinstance(message.data, dict) else {}
        args, session = cfg.get('args', {}) or {}, cfg.get('session', {}) or {}
        fmt = {**(dict(files[0]) if files else {}), **session}   # 用于把模板格式化成实际标题
        info = {
            'files'  : [getattr(f, 'path', None) for f in files],
            'account': args.get('account'),
            'title'  : replace_keywords(args['title'], fmt) if args.get('title') else '',
            'desc'   : replace_keywords(args['desc'], fmt) if args.get('desc') else '',
            'dynamic': replace_keywords(args['dynamic'], fmt) if args.get('dynamic') else '',
            'cover'  : (extra or {}).get('cover'),   # 生成的封面本地路径（失败时由 biliwebapi 带回）
            'error'  : str((message.data or {}).get('error') if isinstance(message.data, dict) else message.msg),
        }
        self._add_hold('upload', group_id, seg_idxs, vtype, info)
        return self.reconcile(group_id, clean_trigger=None)

    def onResume(self, message:PipeMessage):
        """人工回传结果：render/merge 给 mp4 路径、upload 给 'done' 走对应成功路径；
        给 '__ignore__' 则忽略（这段标终态跳过，组照常走完）。"""
        data = message.data or {}
        hold_id, result = data.get('hold_id'), data.get('result')
        hold = self.held_tasks.get(hold_id)
        if not hold:
            self.logger.warning(f'{self.name}: 未找到挂起任务 {hold_id}')
            return []
        group_id, stage, vtype = hold['group_id'], hold['stage'], hold['vtype']
        if group_id not in self.state_dict:
            self.held_tasks.pop(hold_id, None)
            return []

        if result == '__ignore__':
            # 忽略：把挂起的段标成"不参与"（终态，后续合并/上传/清理都跳过），组照常走完释放。
            # 不删硬盘文件，只是把这段从管线里摘出去。
            for idx in hold['segs']:
                slot = self.state_dict[group_id][idx].get(vtype)
                if slot:
                    slot['status'], slot['wait'] = None, []
            self.logger.warning(f'{self.name}: 已忽略挂起任务 {hold_id}（{stage}），该段不再参与后续处理')
            self._drop_cover((hold.get('info') or {}).get('cover'))
            self.held_tasks.pop(hold_id, None)
            return self.reconcile(group_id, clean_trigger=None)

        if stage == 'render':
            seg_idx = hold['segs'][0]
            src = (self.state_dict[group_id][seg_idx].get('src_video_pre') or {}).get('file')
            slot = self.state_dict[group_id][seg_idx][vtype]
            slot['status'], slot['file'], slot['wait'] = 'ready', self._build_videoinfo_from(src, result, vtype), []
        elif stage == 'merge':
            tail_src = None
            for idx in hold['segs']:
                slot = self.state_dict[group_id][idx][vtype]
                slot['status'], slot['wait'] = 'merged', []
                if slot.get('file'):
                    tail_src = slot['file']
            merged = self._build_videoinfo_from(tail_src, result, vtype)
            merged.segment_id = len(self.state_dict[group_id]) + 1
            new_state = {
                'src_video':     {'status': None, 'file': None, 'wait': []},
                'src_video_pre': {'status': None, 'file': None, 'wait': []},
                'dm_video':      {'status': None, 'file': None, 'wait': []},
            }
            new_state[vtype] = {'status': 'ready', 'file': merged, 'wait': []}
            self.state_dict[group_id].append(new_state)
            self.merged_videos.append({'output': str(merged.path), 'type': vtype,
                                       'segments': len(hold['segs']), 'time': datetime.now()})
        elif stage == 'upload':
            for idx in hold['segs']:
                slot = self.state_dict[group_id][idx][vtype]
                slot['status'], slot['wait'] = 'uploaded', []

        self._drop_cover((hold.get('info') or {}).get('cover'))   # 生成的封面用完即删
        self.held_tasks.pop(hold_id, None)
        # upload 完成后接着清理；render/merge 完成后让 reconcile 自然推进（合并/上传）
        return self.reconcile(group_id, clean_trigger='onUploadEnd' if stage == 'upload' else None)

    def _drop_cover(self, path):
        """删除生成的封面本地文件（仅上传挂起会带 cover；None/不存在都安全跳过）。"""
        if not path:
            return
        try:
            os.remove(path)
        except Exception:
            pass

    def _collect_session_data(self, group_id: str):
        """onLiveEnd 时把礼物统计（读完即删 gifts.jsonl）和开播/下播/时长写进 session_data[group_id]，
        供上传格式化标题用。"""
        # 礼物统计
        gift_dm_args = self.config['download_args'].get('gift_dm_args', {})
        if gift_dm_args.get('gift_recorder', False):
            try:
                raw_stat = generate_gift_statistics(
                    jsonl_paths=[str(self.src_path) + '/gifts.jsonl'],
                    stat_path=str(self.src_path) + '/gifts_statistics.jsonl',
                    rank_top=gift_dm_args.get('rank_top_n', 3),
                    delete_after_process=True,
                )
                if raw_stat:
                    names_str = ",".join(
                        f"{item['name']}({item['total_value']})"
                        for item in raw_stat.get("top_ranking", [])
                    )
                    self.session_data[group_id].update({
                        "gifts_revenue" : raw_stat.get("gifts_revenue", ""),
                        "num_of_gifters": raw_stat.get("num_of_gifters", ""),
                        "sc_revenue"    : raw_stat.get("sc_revenue", ""),
                        "num_of_sc"     : raw_stat.get("num_of_sc", ""),
                        "member_revenue": raw_stat.get("member_revenue", ""),
                        "member_info"   : raw_stat.get("member_info", ""),
                        "total_revenue" : raw_stat.get("total_revenue", ""),
                        "top_ranking"   : names_str,
                    })
            except Exception as e:
                self.logger.warning(f'礼物统计生成失败: {e}')

        # 开播/下播/总时长
        try:
            stime, etime = read_last_complete_session(self.src_path)
            self.session_data[group_id].update({
                "stime"    : stime,
                "etime"    : etime,
                "totaltime": format_duration(stime, etime),
            })
        except Exception as e:
            self.logger.warning(f"获取开播/下播时间失败: {e}")

    def onCleanEnd(self, message:PipeMessage):
        """清理真正完成（cleaner 回发 cleaner/end）后，把对应分段从 cleaning 标为 cleaned。
        与 onUploadEnd 同构：按 request_id 在 wait 里定位、移除、wait 空则置终态。"""
        self.logger.info(f'{self.name}: {message.msg}.')
        request_id = message.request_id
        affected = None
        if request_id is not None:
            for group_id, video_states in self.state_dict.items():
                for idx, video_state in enumerate(video_states):
                    for vtype, info in video_state.items():
                        if request_id in info['wait']:
                            info['wait'].remove(request_id)
                            if not info['wait']:
                                info['status'] = 'cleaned'
                            affected = group_id
        return self.reconcile(affected, clean_trigger=None)

    def _default_clean_trigger(self):
        """clean_args 条目不填 trigger 时的默认触发点：清理发生在该文件「最后一个消费阶段」之后。
        优先级：上传 > 合并 > 下播。

        渲染/转码不作为默认清理时机——清理一律在下播之后，而 onRenderEnd 是边录边渲、
        多发生在下播前，用它当默认会过早。渲染场景落到 onLiveEnd（下播后清），下播后才渲完的
        段由 reconcile 的「下播后每轮补查 onLiveEnd」兜住，不会漏。

        注意：上传排在合并之前，当某 vtype「既实时上传又要合并」时这个默认会咬人——实时上传发生在
        录制中(下播前)，但清理已被「下播后」硬下限挡住，真正清是在下播后，那时段可能已被合并消费。
        碰到这种组合，请给该清理规则显式写 trigger: onMergeEnd 盖掉默认。"""
        ev = self.config['common_event_args']
        if ev.get('auto_upload'):
            return 'onUploadEnd'
        if ev.get('auto_merge'):
            return 'onMergeEnd'
        return 'onLiveEnd'

    def _check_for_clean(self, group_id: str, trigger: str):
        """生成清理任务。trigger（onRenderEnd/onUploadEnd/onLiveEnd/onMergeEnd）与 clean_args 各条目的
        trigger 匹配，只处理匹配的条目；条目不填 trigger 则按 _default_clean_trigger 推断默认值。

        trigger 不区分「合并产物 / 被合并的源」，只看是不是这个 vtype 的 trigger：是 dm_video 的
        trigger 就清掉所有落地成型的 dm_video（产物 + 源一并）。只跳过在途/无文件/已清理的：
        None(未参与)、rendering/uploading/merging/cleaning(在途，清了会毁坏)、cleaned(已清)。"""
        ret_msgs = []
        if not self.config['common_event_args'].get('auto_clean'):
            return ret_msgs
        # 硬下限：清理一律在下播之后。本组还没下播(不在 ended_dict)就一律不清，
        # 避免清掉录制中仍可能被渲染/合并用到的文件——任何 trigger 都受此约束。
        if group_id not in self.ended_dict:
            return ret_msgs
        clean_args = self.config.get('clean_args') or {}

        video_states = self.state_dict.get(group_id)
        if not video_states:
            return ret_msgs

        # 落地成型、磁盘上有真实文件、可被清理的状态（不分产物/源）；在途/无文件/已清理的不在此列
        cleanable = ('ready', 'uploaded', 'merged')

        for idx, video_state in enumerate(video_states):
            for vtype, info in video_state.items():
                # 只清理与本 trigger 匹配状态的真实文件；其余状态(merging/cleaning/cleaned/None 等)一律跳过
                if info.get('status') not in cleanable:
                    continue
                for clean_file_types, clean_arg in clean_args.items():
                    if vtype not in clean_file_types.split('+') and clean_file_types != 'all':
                        continue
                    for arg in clean_arg:
                        # trigger 不填则按开关推断默认值（上传>合并>渲染/转码>下播）
                        if (arg.get('trigger') or self._default_clean_trigger()) != trigger:
                            continue
                        if file := info['file']:
                            clean_msg = PipeMessage(
                                source=self.name,
                                target='cleaner',
                                event='newtask',
                                request_id=uuid(),
                                data={
                                    'taskname': self.name,
                                    'files'   : [file],
                                    'method'  : arg['method'],
                                    'delay'   : arg.get('delay', 0),
                                    'args'    : arg,
                                }
                            )
                            # 提交时标 cleaning + 挂 request_id；真正清理完成后由 onCleanEnd 标 cleaned
                            self.state_dict[group_id][idx][vtype]['status'] = 'cleaning'
                            self.state_dict[group_id][idx][vtype]['wait'].append(clean_msg.request_id)
                            self.logger.debug(f'清理触发（{trigger}）: {file}')
                            ret_msgs.append(clean_msg)

        return ret_msgs
    
    def _group_is_done(self, group_id, ignore_cleaning=False):
        """这一组的所有段是否都到终态、且无在途任务（只回答“完事没”，不做回收）。
        ignore_cleaning=True 时把「已派给 cleaner 的 cleaning」也当完事——仅供 is_idle 判断能否空闲
        重启用（cleaner 写了 pending_cleanup.json、能跨重启续删）。常规内存释放不开它：要等清理真
        完成(onCleanEnd 标 cleaned)再释放，免得 .session 镜像和状态在清理途中过早消失。"""
        video_states = self.state_dict.get(group_id)
        if not video_states:
            return True   # state_dict 已无此组 = 没有未完成的工作
        final_status = ['ready']
        if self.config['common_event_args'].get('auto_upload'):
            final_status.append('uploaded')
        if self.config['common_event_args'].get('auto_clean'):
            final_status.append('cleaned')
        # None=未参与；merged=已被合并消费；cleaned=已清理（即便 auto_clean 关，merger remove_source
        # 删源也会标它）：恒为终态。cleaning 默认不算终态，只有 ignore_cleaning(重启判断)时才放行。
        terminal = ('merged', 'cleaned') + (('cleaning',) if ignore_cleaning else ())
        for video_state in video_states:
            for info in video_state.values():
                st = info.get('status')
                if st is None or st in terminal:
                    continue
                if st not in final_status:
                    return False            # rendering/uploading/merging 等真在途任务，不能释放
                if info.get('wait'):
                    return False            # 还有任务在途，不能释放
        return True

    def is_idle_for_restart(self):
        """空闲重启判断：除「已交给 cleaner 的清理」外，无在途/待处理的管线工作。
        清理可跨重启续做(pending_cleanup.json)，故不阻塞重启；渲染/上传/合并随重启即丢，仍要等完。"""
        return all(self._group_is_done(gid, ignore_cleaning=True)
                   for gid in list(self.state_dict.keys()))

    def _free_group(self, group_id):
        """回收某组的内存记账（state/ended/session_data/挂起项）+ 落盘记录。"""
        self.ended_dict.pop(group_id, None)
        self.state_dict.pop(group_id, None)
        self.session_data.pop(group_id, None)
        self.held_tasks = {h: v for h, v in self.held_tasks.items() if v['group_id'] != group_id}
        self._delete_record(group_id)

    def _free_state_memory(self):
        # 正常回收：某场的全部异步工作（渲染/转码/上传/清理/合并）都到终态后释放它。
        for group_id in list(self.ended_dict.keys()):
            if self._group_is_done(group_id):
                self.logger.info(f'视频组{group_id}处理完成，视频信息已被释放.')
                self._free_group(group_id)

        # 超时兜底：到不了终态的组（含挂起等人工的）超过 state_timeout_hours 强制回收。
        # 注意：挂起任务不豁免——人若一直不处理，到点会被一起清掉（时长见配置 common_event_args.state_timeout_hours）。
        timeout_h = self.config['common_event_args'].get('state_timeout_hours', 72)
        for group_id in list(self.ended_dict.keys()):
            if time.time() - self.ended_dict[group_id] > timeout_h * 3600:
                self.logger.info(f'视频组{group_id}处理超时（>{timeout_h}h），视频信息将被释放.')
                self._free_group(group_id)

    def onExit(self, *args, **kwargs) -> None:
        self.logger.info(f'{self.name}: 任务结束.')
        # 退出即整体释放：清掉全部 per-group 内存记账（对齐 _free_group 清的那几样）
        self.state_dict.clear()
        self.ended_dict.clear()
        self.session_data.clear()
        self.held_tasks.clear()
        self.merged_videos.clear()
        # liveevents 随程序退出而死，其内存镜像(.session_*.json)也一并删掉。
        delete_all_records(str(self.config['download_args']['output_dir']))
        return PipeMessage(
            source=self.name,
            target='downloader',
            event='stoptask',
            data=self.name,
        )
