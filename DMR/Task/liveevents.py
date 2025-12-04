import logging
import os
from .baseevents import BaseEvents
from .merge_mp4 import *
from ..utils import *
from pathlib import Path

class LiveEvents(BaseEvents): # 被 class ReplayTask()初始化
    def __init__(self, name, config):
        super().__init__(name, config)
        self.state_dict = {}
        self.ended_dict = {}
        self.logger = logging.getLogger(__name__)
        self.is_sync= False
        self.bvid=None # 在这里初始化防止先进入了onLiveEnd 需要判断self.bvid

    @property
    def event_dict(self):
        # 注意： 这是一个property
        # 被上层  ReplayTask() 的 start函数调用
        # for event, trigger in self.event_class.event_dict.items():
        return {
            'ready': self.onReady,
            'exit': self.onExit,
            'downloader/livestart': self.onLiveStart,
            'downloader/livesegment': self.onLiveSegment,
            'downloader/liveend': self.onLiveEnd,
            'downloader/livestop': self.onLiveEnd,
            'render/end': self.onRenderEnd,
            'render/error': self.defaultEvent,
            'uploader/end': self.onUploadEnd,
            'uploader/error': self.defaultEvent,
            'cleaner/end': self.defaultEvent,
            'cleaner/error': self.defaultEvent,
            'default': self.defaultEvent,
        }

    def defaultEvent(self, message:PipeMessage):
        self.logger.info(f'{self.name}: {message.msg}')

    def onLiveStart(self, message:PipeMessage):
        self.logger.info(f'{self.name}: {message.msg}')
        self.is_add_to_list  = False
        self.is_live_end     = False
        self.is_desc_offtime = False
        self.bvid            = None
        self.src_path        = Path(str(self.config['download_args']['output_dir']))
        self.dmvideo_path    = Path(str(self.config['download_args']['output_dir']) + '（弹幕版）')
        self.transcode_path  = Path(str(self.config['download_args']['output_dir']) + '（转码后）')
        self.check_sync_list_name()
        self.check_render_cover()

    def check_render_cover(self):
        cover_args=self.config['common_event_args'].get("cover_args", {})
        is_render_cover=cover_args.get("is_render_cover")
        if is_render_cover: # 渲染封面
            _name=cover_args.get("name","")
            _now=datetime.now()
            _time=f"{_now.month}月{_now.day}日"
            _color=cover_args.get("name_color","#111111")
            output_dir=cover_args.get("output_dir","./covers")
            from DMR.utils.render_with_manimgl import rendercover_with_manimgl_bg
            rendercover_with_manimgl_bg(_name,_time,_color,output_dir=output_dir)

    def check_sync_list_name(self):
        is_sync   = self.config['common_event_args'].get("sync_list_name", {}).get("is_sync")
        if not is_sync or self.is_sync:
            # self.is_sync会在onLiveEnd和初始化的时候标记为 false
            # self.is_sync会在sync后标记为true
            self.logger.debug("不同步视频名为列表视频名")
            return
        else:
            account   = self.config['common_event_args'].get("sync_list_name", {}).get("account")
            sectionId = self.config['common_event_args'].get("sync_list_name", {}).get("sectionId")
            try:
                sync_section_episode_titles_bg(account,sectionId)
                self.is_sync = True
            except Exception:
                # 内部会有logger信息
                pass

    def _state_snapshot(self):
        """只提取可读信息：status / file.path / wait"""
        snap = {}
        for gid, vss in self.state_dict.items():
            snap[gid] = []
            for vs in vss:
                row = {}
                for vt, info in vs.items():
                    f = info.get('file')
                    row[vt] = {
                        'status': info.get('status'),
                        'wait': list(info.get('wait') or []),
                        'file': getattr(f, 'path', str(f)) if f else None,
                    }
                snap[gid].append(row)
        return snap

    def _log_state(self, where, extra=None):
        snap = self._state_snapshot()
        self.logger.debug(f"[STATE@{where}] {snap} {(' '+str(extra)) if extra else ''}")


    def onReady(self, *args, **kwargs): # 这里被engine的add_task发送了一个ready信息
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
            # 'video_id': uuid(8),
            'src_video': {'status': None, 'file': None, 'wait': []},
            'src_video_pre': {'status': None, 'file': None, 'wait': []},
            'dm_video': {'status': None, 'file': None, 'wait': []},
        }
        if self.state_dict.get(video.group_id):
            self.state_dict[video.group_id].append(video_state)
        else:
            self.state_dict[video.group_id] = [video_state]

        ret_msgs = []
        if self.config['common_event_args'].get('auto_transcode'):
            transcode_args = self.config['render_args']['transcode']
            if transcode_args.get('output_name'):
                filename = replace_keywords(transcode_args['output_name'], video, replace_invalid=True) + \
                        f".{transcode_args.get('format','mp4')}"
            else:
                filename = os.path.splitext(os.path.basename(video.path))[0] + \
                        f"（转码后）.{transcode_args.get('format','mp4')}"
            if transcode_args.get('output_dir'):
                output_dir = transcode_args.get('output_dir')
            else:
                output_dir = os.path.dirname(video.path) + '（转码后）'
            output = os.path.join(output_dir, filename)
            transcode_msg = PipeMessage(
                source=self.name,
                target='render',
                event='newtask',
                request_id=uuid(),
                data={
                    'taskname': self.name,
                    'mode': 'transcode',
                    'video': video,
                    'output': output,
                    'args': transcode_args,
                }
            )
            self.state_dict[video.group_id][-1]['src_video_pre'].update({'status': 'ready', 'file': video})
            self.state_dict[video.group_id][-1]['src_video']['status'] = 'rendering'
            self.state_dict[video.group_id][-1]['src_video']['wait'].append(transcode_msg.request_id)
            ret_msgs.append(transcode_msg)
        else:
            # self.state_dict[video.group_id][-1]['src_video'].update({'status': 'ready', 'file': video})
            # 此处修改为就算不转码flv也放在src_video_pre里，方便清理
            self.state_dict[video.group_id][-1]['src_video_pre'].update({'status': 'ready', 'file': video})
        
        if self.config['common_event_args'].get('auto_render'):
            render_args = self.config['render_args']['dmrender']
            if render_args.get('output_name'):
                filename = replace_keywords(render_args['output_name'], video, replace_invalid=True) + \
                        f".{render_args.get('format','mp4')}"
            else:
                filename = os.path.splitext(os.path.basename(video.path))[0] + \
                        f"（弹幕版）.{render_args.get('format','mp4')}"
            if render_args.get('output_dir'):
                output_dir = render_args.get('output_dir')
            else:
                output_dir = os.path.dirname(video.path) + '（弹幕版）'
            output = os.path.join(output_dir, filename)
            render_msg = PipeMessage(
                source=self.name,
                target='render',
                event='newtask',
                request_id=uuid(),
                data={
                    'taskname': self.name,
                    'mode': 'dmrender',
                    'video': video,
                    'output': output,
                    'args': render_args,
                }
            )
            self.state_dict[video.group_id][-1]['dm_video']['status'] = 'rendering'
            self.state_dict[video.group_id][-1]['dm_video']['wait'].append(render_msg.request_id)
            ret_msgs.append(render_msg)

        if self.config['common_event_args'].get('auto_upload'):
            ret_msgs += self._check_for_upload(video.group_id, len(self.state_dict[video.group_id])-1)

        # onLiveSegment 结尾处----------------------
        # self._log_state("onLiveSegment:end", extra={"auto_transcode": self.config['common_event_args'].get('auto_transcode'),
        #                                         "auto_render": self.config['common_event_args'].get('auto_render')})

        return ret_msgs
    
    def onLiveEnd(self, message:PipeMessage):
        self.is_live_end=True
        self.is_sync= False # 给下一次开播 重新同步视频名和列表名做准备
        self.logger.info(f'{self.name}: {message.msg}.')
        group_id = message.data
        if group_id is None:
            return

        # 先处理 state_dict
        if group_id in self.state_dict:
            self.ended_dict[group_id] = time.time()
        else:
            self.logger.debug(f'No such group:{group_id}.')

        # 检查是否合并
        self.check_for_merge(group_id)

        # ---------------- 佐佐视频：渲染 + 上传 + 加合集 ----------------
        zuozuo_video_args=self.config['common_event_args'].get('zuozuo_video_args',{})
        if zuozuo_video_args.get("is_render",False):
            from DMR.utils.render_with_manimgl import render_zuozuovideo_with_manimgl
            stime     = read_live_time_from_path(self.src_path, is_start=True)
            etime     = read_live_time_from_path(self.src_path, is_start=False)
            duration  = format_duration(stime, etime)
            self.logger.info("开始渲染佐佐视频")
            try:
                zuozuo_video_path = render_zuozuovideo_with_manimgl(self.src_path)
            except Exception as e:
                self.logger.error(f"佐佐视频渲染失败: {e}")
                # 渲染都失败了，后面上传/加合集就完全没意义，直接跳过佐佐逻辑
                zuozuo_video_path = None

            if zuozuo_video_args.get("is_upload",False) and zuozuo_video_path:
                from DMR.utils.upload_video import upload_zuozuo_video
                # self.logger.info("开始上传佐佐视频")
                success, bvid, log_text = upload_zuozuo_video(
                    str(zuozuo_video_path),
                    stime,
                    etime,
                    duration,
                    is_only_self=zuozuo_video_args.get("is_only_self",True),
                    cover_path=self.config['common_event_args'].get('cover_args',{}).get("output_dir")+"/cover.png"
                )

                if not success or not bvid:
                    self.logger.error(f"佐佐视频上传失败，不加入合集。上传日志：\n{log_text}")
                else:
                    sectionId = parse_sectionId("zuo")
                    self.logger.info(
                        f"佐佐视频上传成功，bvid={bvid}，准备加入合集 sectionId={sectionId}"
                    )
                    try:
                        add_to_list(bvid,sectionId)
                    except Exception as e:
                        self.logger.error(f"佐佐视频加入合集/同步标题失败: {e}")


        # ---------------- 正常 auto_upload 流程 ----------------
        ret_msgs = []
        if self.config['common_event_args'].get('auto_upload'):
            upload_msgs = self._check_for_upload(group_id)
            ret_msgs += upload_msgs

        self._free_state_memory()


        # ---------------- 写下播时间到简介 ----------------
        dm_video = self.config.get('upload_args', {}).get('dm_video')
        if dm_video: # 只有配置了 dm_video 时才考虑写简介
            time_template=dm_video[0].get('add_livetime_todesc')
            if time_template and self.bvid and not self.is_desc_offtime:
                self.add_livetime_wrapper(time_template,self.bvid)
        

        return ret_msgs
    
    def add_livetime_wrapper(self,time_template,bvid):
        dm_video = self.config.get('upload_args', {}).get('dm_video')
        account=dm_video[0].get('account')
        start_time= read_live_time_from_path(self.src_path,is_start=True)
        end_time  = read_live_time_from_path(self.src_path,is_start=False)
        duration  = format_duration(start_time,end_time)
        result = replace_keywords(
            time_template,
            {"stime": start_time,
             "etime": end_time,
             "totaltime":duration}
             )
        try:
            add_livetime(bvid,result,account)
            self.is_desc_offtime=True
        except Exception as e:
            self.logger.error(e)


    def _check_for_upload(self, group_id:str, _idx:int=None):
        # self._log_state("Before _check_for_upload")
        ret_msgs = []
        if not self.state_dict.get(group_id):
            return ret_msgs
        
        upload_args = self.config['upload_args']

        for idx, video_state in enumerate(self.state_dict[group_id]):
            if _idx is not None and idx != _idx:
                continue
            for vtype, info in video_state.items():
                if info['status'] != 'ready':
                    continue
                for upload_file_types, upload_arg in upload_args.items():
                    # 判断当前视频是否需要上传
                    if vtype in upload_file_types.split('+'):
                        for upid, arg in enumerate(upload_arg):
                            # 实时上传
                            if not arg.get('realtime'):
                                continue
                            if info['file'].duration < arg.get('min_length', 0):
                                self.logger.info(f'视频{info["file"].path}时长为{info["file"].duration}s，设置{arg.get("min_length", 0)}s，跳过上传.')
                                continue
                            upload_group_id = info['file'].upload_group_id if hasattr(info['file'], 'upload_group_id') else group_id
                            upload_msg = PipeMessage(
                                source=self.name,
                                target='uploader',
                                event='newtask',
                                request_id=uuid(),
                                data={
                                    'taskname': self.name,
                                    'files': [info['file']],
                                    'engine': arg['engine'],
                                    'stateless': False,
                                    'upload_group': upload_group_id+'_'+upload_file_types+'_'+str(upid),
                                    'args': arg,
                                }
                            )
                            # self.logger.debug(
                            #     "UploadMessage Created(实时):\n"
                            #     f"  source      = {upload_msg.source}\n"
                            #     f"  target      = {upload_msg.target}\n"
                            #     f"  event       = {upload_msg.event}\n"
                            #     f"  request_id  = {upload_msg.request_id}\n"
                            #     f"  upload_group= {upload_msg.data.get('upload_group')}\n"
                            #     f"  files       = {upload_msg.data.get('files')}\n"
                            #     f"  engine      = {upload_msg.data.get('engine')}\n"
                            #     f"  stateless   = {upload_msg.data.get('stateless')}\n"
                            #     f"  args        = {upload_msg.data.get('args')}"
                            # )

                            self.state_dict[group_id][idx][vtype]['status'] = 'uploading'
                            self.state_dict[group_id][idx][vtype]['wait'].append(upload_msg.request_id)
                            ret_msgs.append(upload_msg)
        
        # 如果当前视频组已经被标记结束，检查是否有视频组完全准备好上传（用于非实时上传）
        if group_id in self.ended_dict:
            # 遍历所有视频类型
            video_types = list(self.state_dict[group_id][-1].keys())
            for vtype in video_types:
                # 检查是否全部准备上传
                videos = []
                for idx, video_state in enumerate(self.state_dict[group_id]):
                    if video_state[vtype]['status'] == 'ready':
                        videos.append(video_state[vtype]['file'])
                    elif video_state[vtype]['status'] == 'merged':
                        continue
                    else:
                        videos = []
                        break
                if not videos:
                    continue

                # 判断当前视频是否需要上传
                for upload_file_types, upload_arg in upload_args.items():
                    if vtype in upload_file_types.split('+'):
                        for upid, arg in enumerate(upload_arg):
                            # 此处只做非实时上传
                            if arg.get('realtime'):
                                continue
                            up_videos = [video for video in videos if video.duration >= arg.get('min_length', 0)]
                            upload_group_id = up_videos[0].upload_group_id if hasattr(up_videos[0], 'upload_group_id') else group_id
                            upload_msg = PipeMessage(
                                source=self.name,
                                target='uploader',
                                event='newtask',
                                request_id=uuid(),
                                data={
                                    'taskname': self.name,
                                    'files': up_videos,
                                    'engine': arg['engine'],
                                    'stateless': True,
                                    'upload_group': upload_group_id+'_'+upload_file_types+'_'+str(upid),
                                    'args': arg,
                                }
                            )
                            # self.logger.debug(
                            #     "UploadMessage Created(非实时):\n"
                            #     f"  source      = {upload_msg.source}\n"
                            #     f"  target      = {upload_msg.target}\n"
                            #     f"  event       = {upload_msg.event}\n"
                            #     f"  request_id  = {upload_msg.request_id}\n"
                            #     f"  upload_group= {upload_msg.data.get('upload_group')}\n"
                            #     f"  files       = {upload_msg.data.get('files')}\n"
                            #     f"  engine      = {upload_msg.data.get('engine')}\n"
                            #     f"  stateless   = {upload_msg.data.get('stateless')}\n"
                            #     f"  args        = {upload_msg.data.get('args')}"
                            # )
                            # 标记状态信息
                            for idx, _ in enumerate(self.state_dict[group_id]):
                                self.state_dict[group_id][idx][vtype]['status'] = 'uploading'
                                self.state_dict[group_id][idx][vtype]['wait'].append(upload_msg.request_id)
                            ret_msgs.append(upload_msg)

        return ret_msgs
    
    def onRenderEnd(self, message:PipeMessage):
        self.logger.info(f'{self.name}: {message.msg}.')
        # debug-------------------------------
        # self._log_state("onRenderEnd:before", extra={"request_id": message.request_id})
        # debug-------------------------------

        request_id = message.request_id
        video:VideoInfo = message.data.get('output')
        video_states = self.state_dict[video.group_id]
        # 将状态信息中request_id对应的等待移除
        for idx, video_state in enumerate(video_states):
            for vtype, info in video_state.items():
                if request_id in info['wait']:
                    self.state_dict[video.group_id][idx][vtype]['wait'].remove(request_id)
                    if len(self.state_dict[video.group_id][idx][vtype]['wait']) == 0:
                        self.state_dict[video.group_id][idx][vtype]['status'] = 'ready'
                        self.state_dict[video.group_id][idx][vtype]['file'] = video
        
        # debug-------------------------------
        # self._log_state("onRenderEnd:middle1", extra={"request_id": message.request_id})
        # debug-------------------------------

        # 检查是否合并
        self.check_for_merge(video.group_id)
        
        # debug-------------------------------
        # self._log_state("onRenderEnd:middle2", extra={"request_id": message.request_id})
        # debug-------------------------------

        ret_msgs = []
        if self.config['common_event_args'].get('auto_upload'):
            upload_msgs = self._check_for_upload(video.group_id)
            ret_msgs += upload_msgs

        # debug-------------------------------
        # self._log_state("onRenderEnd:after")
        # debug-------------------------------

        return ret_msgs
    def check_for_merge(self, group_id):
        # is_merge / merge_type / 音量相关配置
        merge_cfg      = self.config['common_event_args'].get("merge_args", {}) or {}
        is_merge       = merge_cfg.get("is_merge")
        merge_type     = merge_cfg.get("merge_type")  # e.g. ['src_video', 'dm_video']
        is_amplify     = merge_cfg.get("is_amplify", True)
        extra_gain_db  = merge_cfg.get("extra_gain_db", 0)

        # 基础防御：不开启合并 / 没有这个 group / merge_type 为空 → 直接退出
        if (not is_merge) or (group_id not in self.ended_dict) or (not merge_type):
            return

        # 所有 seg 的起止时间（你原来的逻辑）
        stime = read_live_time_from_path(self.src_path, is_start=True)
        etime = read_live_time_from_path(self.src_path, is_start=False)

        # vt -> 目标 slot 名（你原来就是这两个）
        target_slot = {
            'src_video': 'src_video',
            'dm_video':  'dm_video',
        }

        # 新占位：先建一个空壳，后面每个 vt 填自己那一格
        new_state = {
            'src_video':     {'status': None, 'file': None, 'wait': []},
            'src_video_pre': {'status': None, 'file': None, 'wait': []},
            'dm_video':      {'status': None, 'file': None, 'wait': []},
        }
        new_seg_id = len(self.state_dict[group_id]) + 1

        # ---------- 小函数：只管 merge 某一种 vt ----------
        def _merge_one_type(vt: str) -> bool:
            """
            只合并某一种 vt：
              1. 检查该 vt 所有分段是否 ready/uploaded
              2. 标记 merging
              3. 调用 merge_amplify_mp4
              4. 成功则标记老分段为 merged，并在 new_state 里写入新的 VideoInfo
              5. 失败则回滚这一种 vt 的状态
            返回:
              True  表示该 vt 合并成功
              False 表示该 vt 当前不应该合并（比如有未 ready 的段）
            """
            # 1. 先检查状态：有任何不是 ready/uploaded 的就直接退出
            entries = []
            for vs in self.state_dict[group_id]:
                entry = vs.get(vt)
                if entry is None:
                    self.logger.debug("group %s 中缺少类型 %s 的占位", group_id, vt)
                    return False
                if entry['status'] not in ('ready', 'uploaded'):
                    self.logger.debug(
                        "退出 %s 合并：存在未 ready/uploaded 的分段 → %s",
                        vt, entry['status']
                    )
                    return False
                entries.append(entry)

            if not entries:
                self.logger.debug("group %s 的类型 %s 没有任何分段，跳过", group_id, vt)
                return False

            # 2. 标记为 merging，并记录旧状态，方便回滚
            changed = []
            for entry in entries:
                changed.append((entry, entry['status']))
                entry['status'] = 'merging'

            # 3. 收集路径、找一个 tail 方便拷贝元信息
            paths = [e['file'].path for e in entries]
            tail  = entries[-1]['file']  # 你原来就是用最后一个作 tail

            self.logger.info("准备合并类型 %s：共 %d 段", vt, len(paths))

            try:
                output_path = merge_mp4(paths,remover=True)
                if is_amplify:
                    output_path= amplify_mp4(
                        output_path,
                        target_db=-1,
                        extra_gain_db=extra_gain_db,
                        remover=True)
                meta=probe_media(output_path)
            except Exception as e:
                # 4. 合并失败：只回滚这一种 vt 的状态
                for entry, old_status in changed:
                    entry['status'] = old_status
                self.logger.debug("合并类型 %s 失败: %s", vt, e)
                self.logger.info("合并类型 %s 失败", vt)
                return False

            # 5. 合并成功：旧分段标记为 merged
            for entry, _ in changed:
                entry['status'] = 'merged'

            # 6. 写新占位：只填这个 vt 对应的 slot
            dst = target_slot.get(vt, vt)  # 默认映射自己
            newvideo = VideoInfo(
                path      = str(output_path),
                dtype     = dst,
                file_id   = uuid(),
                size      = os.path.getsize(output_path),
                ctime     = datetime.now(),
                stime     = stime,
                etime     = etime,
                dm_file_id= "",
                duration  = meta.get('duration') or 0,
                segment_id= new_seg_id,
                taskname  = tail.taskname,
                group_id  = group_id,
                streamer  = tail.streamer,
                title     = tail.title,
                resolution= meta.get('resolution') or (0,0),
            )
            new_state[dst] = {'status': 'ready', 'file': newvideo, 'wait': []}

            self.logger.info("类型 %s 合并成功 → %s", vt, output_path)
            return True

        # ---------- 外层：对每一种 merge_type 单独调用小函数 ----------
        any_merged = False
        for vt in merge_type:
            ok = _merge_one_type(vt)
            if ok:
                any_merged = True

        # 如果一个都没合并成功，就不追加新占位
        if not any_merged:
            self.logger.info("check_for_merge: 所有 merge_type 都未合并成功，结束")
            return

        # 至少有一个 vt 合并成功：追加新的 state
        self.state_dict[group_id].append(new_state)

    # def check_for_merge(self, group_id):
    #     # 保留：所有都 ready 才能合并（all-ready gate）
    #     # self.logger.info('START:check_for_merge')
    #     # self._log_state('check_for_merge-BEFORE')
    #     is_merge   = self.config['common_event_args'].get("merge_args", {}).get("is_merge")
    #     merge_type = self.config['common_event_args'].get("merge_args", {}).get("merge_type")
    #     is_amplify = self.config['common_event_args'].get("merge_args", {}).get("is_amplify", True)
    #     extra_gain_db = self.config['common_event_args'].get("merge_args", {}).get("extra_gain_db", 0)
    #     if not is_merge or group_id not in self.ended_dict:
    #         # reason = []
    #         # if not merge_type:
    #         #     reason.append("merge_type 为空或未配置")
    #         # if group_id not in self.ended_dict:
    #         #     reason.append(f"group_id {group_id} 不在 ended_dict 中")
    #         # self.logger.debug(f"退出 check_for_merge（原因：{', '.join(reason)}）")
    #         return

    #     for vt in merge_type:
    #         for vs in self.state_dict[group_id]:
    #             if vs[vt]['status'] not in ('ready', 'uploaded'):
    #                 self.logger.debug(
    #                 f"退出 check_for_merge：{vt} 中存在未 ready/uploaded 的分段 → {vs[vt]['status']}"
    #                 )
    #                 return

    #     stime = read_live_time_from_path(self.src_path, is_start=True)
    #     etime = read_live_time_from_path(self.src_path, is_start=False)
    #     target_slot = {'src_video': 'src_video', 'dm_video': 'dm_video'}

    #     # 收集合并、标记 merging
    #     changed = {vt: [] for vt in merge_type}
    #     groups  = {vt: [] for vt in merge_type}        # 路径
    #     tails   = {vt: None for vt in merge_type}      # 用来拷贝元信息
    #     for vt in merge_type:
    #         for vs in self.state_dict[group_id]:
    #             entry = vs[vt]
    #             entry['status'] = 'merging'
    #             changed[vt].append(entry)
    #             groups[vt].append(entry['file'].path)
    #             tails[vt] = entry['file']
    #     self.logger.info("准备合并：%s"," | ".join(f"{vt}:{len(groups[vt])}段" for vt in merge_type))

    #     try:
    #         # 逐类型合并
    #         merged = {}  # vt -> (path, meta)
    #         for vt in merge_type:
    #             merged_path, meta = merge_amplify_mp4(
    #                 groups[vt],
    #                 remover=True,
    #                 is_amplify=is_amplify,
    #                 extra_gain_db=extra_gain_db,
    #             )
    #             merged[vt] = (merged_path, meta)

    #     except Exception as e:
    #         # 失败回滚
    #         for vt in merge_type:
    #             for entry in changed[vt]:
    #                 entry['status'] = 'ready'
    #         self.logger.debug("合并失败: %s", e)
    #         self.logger.info("合并失败")
    #         return

    #     # 成功：旧分段标记 merged
    #     for vt in merge_type:
    #         for entry in changed[vt]:
    #             entry['status'] = 'merged'

    #     # 组装新占位（只填我们这次做的类型）
    #     new_state = {
    #         'src_video':     {'status': None, 'file': None, 'wait': []},
    #         'src_video_pre': {'status': None, 'file': None, 'wait': []},
    #         'dm_video':      {'status': None, 'file': None, 'wait': []},
    #     }
    #     new_seg_id = len(self.state_dict[group_id]) + 1

    #     for vt in merge_type:
    #         out_path, meta = merged[vt]
    #         dst = target_slot.get(vt, vt)
    #         tail = tails[vt]
    #         newvideo = VideoInfo(
    #             path=out_path,
    #             dtype=dst,
    #             file_id=uuid(),
    #             size=os.path.getsize(out_path),
    #             ctime=datetime.now(),
    #             stime=stime,
    #             etime=etime,
    #             dm_file_id=None,
    #             duration=meta.get('duration'),
    #             segment_id=new_seg_id,
    #             taskname=tail.taskname,
    #             group_id=group_id,
    #             streamer=tail.streamer,
    #             title=tail.title,
    #             resolution=meta.get('resolution'),
    #         )
    #         new_state[dst] = {'status': 'ready', 'file': newvideo, 'wait': []}

    #     self.state_dict[group_id].append(new_state)
    
    def _check_for_clean(self, group_id=None):
        ret_msgs = []
        clean_args = self.config['clean_args']
        # debug-----------------
        # self._log_state("check_for_clean:enter")
        # self.logger.debug(f"[CLEAN] clean_args={clean_args}")
        # debug-----------------
        for group_id, video_states in self.state_dict.items():
            for idx, video_state in enumerate(video_states):
                for vtype, info in video_state.items():
                    if info.get('status') != 'uploaded':
                        # debug-------------------------------------------
                        # self.logger.debug(f"[CLEAN] skip gid={group_id} idx={idx} vtype={vtype} status={info['status']} (need 'uploaded')")
                        continue
                    for clean_file_types, clean_arg in clean_args.items():
                        # debug-------------------------------------------
                        # self.logger.debug(f"[CLEAN] rule={clean_file_types} vtype={vtype} "
                        #                   f"match={vtype in clean_file_types.split('+') or clean_file_types == 'all'} "
                        #                   f"type(clean_arg)={type(clean_arg)}")
                        # debug-------------------------------------------
                        # 判断当前视频是否需要清理
                        if vtype in clean_file_types.split('+') or clean_file_types == 'all':
                            for arg in clean_arg:
                                files = [info['file']]

                                # 判断是否需要清理源文件
                                # if vtype == 'dm_video' and arg.get('w_srcfile', False) == True and video_state['src_video']['file'] is not None:
                                #     files.append(video_state['src_video']['file'])
                                #     self.state_dict[group_id][idx]['src_video']['status'] = 'cleaned'

                                # w_srcfile为转码文件
                                if arg.get('w_srcfile', False) == True and video_state['src_video']['file'] is not None:
                                    files.append(video_state['src_video']['file'])
                                    # self.state_dict[group_id][idx]['src_video']['status'] = 'cleaned'

                                # 判断是否需要清理源文件（转码前）
                                # if vtype == 'src_video' and arg.get('w_srcpre', True) == True and video_state['src_video_pre']['file'] is not None:
                                #     files.append(video_state['src_video_pre']['file'])
                                #     self.state_dict[group_id][idx]['src_video_pre']['status'] = 'cleaned'

                                # w_srcpre为flv源文件
                                if arg.get('w_srcpre', True) == True and video_state['src_video_pre']['file'] is not None:
                                    files.append(video_state['src_video_pre']['file'])
                                    # self.state_dict[group_id][idx]['src_video_pre']['status'] = 'cleaned'
                                
                                # debug-----------------
                                # file_names = [str(getattr(f, 'path', f)) for f in files if f]
                                # self.logger.debug(f"[CLEAN] group={group_id} idx={idx} vtype={vtype} "
                                #                   f"→ method={arg['method']} delay={arg['delay']} files={file_names}")
                                # debug-----------------

                                clean_msg = PipeMessage(
                                    source=self.name,
                                    target='cleaner',
                                    event='newtask',
                                    request_id=uuid(),
                                    data={
                                        'taskname': self.name,
                                        'files': files,
                                        'method': arg['method'],
                                        'delay': arg['delay'],
                                        'args': arg,
                                    }
                                )
                                ret_msgs.append(clean_msg)

                    # self.state_dict[group_id][idx][vtype]['status'] = 'cleaned'

        # debug-----------------
        # self.logger.debug(f"[CLEAN] done, total tasks={len(ret_msgs)}")
        # self._log_state("check_for_clean:after")
        # debug-----------------
        return ret_msgs
    
    def _free_state_memory(self):
        final_status = 'ready'
        if self.config['common_event_args'].get('auto_upload'):
            final_status = 'uploaded'
        if self.config['common_event_args'].get('auto_clean'):
            final_status = 'cleaned'
        
        for group_id in list(self.ended_dict.keys()):
            need_free = True
            for idx, video_state in enumerate(self.state_dict[group_id]):
                for vtype, info in video_state.items():
                    if info['status'] is not None and info['status'] != final_status:
                        need_free = False
                        break
                if not need_free: break
            if need_free:
                self.logger.debug(f'视频组{group_id}处理完成，视频信息已被释放.')
                self.ended_dict.pop(group_id)
                self.state_dict.pop(group_id)

        for group_id in list(self.ended_dict.keys()):
            if time.time() - self.ended_dict[group_id] > 72*3600:
                self.logger.debug(f'视频组{group_id}处理超时，视频信息将被释放.')
                self.ended_dict.pop(group_id)
                self.state_dict.pop(group_id)

    def onUploadEnd(self, message:PipeMessage):
        self.logger.info(f'{self.name}: {message.msg}.')
        #debug------------------
        # self._log_state("onUploadEnd:before", extra={"request_id": message.request_id})
        #debug------------------

        request_id = message.request_id
        # 将状态信息中request_id对应的等待移除
        for group_id, video_states in self.state_dict.items():
            for idx, video_state in enumerate(video_states):
                for vtype, info in video_state.items():
                    if request_id in info['wait']:
                        self.state_dict[group_id][idx][vtype]['wait'].remove(request_id)
                        if len(self.state_dict[group_id][idx][vtype]['wait']) == 0:
                            self.state_dict[group_id][idx][vtype]['status'] = 'uploaded'
        #debug------------------
        # self._log_state("onUploadEnd:middle", extra={"request_id": message.request_id})
        #debug------------------

        ret_msgs = []
        if self.config['common_event_args'].get('auto_clean'):
            clean_msgs = self._check_for_clean()
            ret_msgs += clean_msgs

        # 判断是否加入合集
        self.bvid=message.bvid
        sectionId = None
        dm_video = self.config.get('upload_args', {}).get('dm_video')
        account=dm_video[0].get('account')
        sectionId = dm_video[0].get('sectionId')
        if sectionId and not self.is_add_to_list:
            # 开始加入合集
            try:
                add_to_list(self.bvid,sectionId,account)
                self.is_add_to_list=True
            except Exception as e:
                self.logger.error(e)

        # 判断是否写入下播时间到简介里
        time_template=dm_video[0].get('add_livetime_todesc')
        if time_template and self.is_live_end and not self.is_desc_offtime:
            self.add_livetime_wrapper(time_template,self.bvid)
        #debug-------------
        # self._log_state("onRenderEnd:after")
        #debug-------------
        return ret_msgs

    def onExit(self, *args, **kwargs) -> None:
        self.logger.info(f'{self.name}: 任务结束.')
        self.state_dict.clear()
        self.ended_dict.clear()
        return PipeMessage(
            source=self.name,
            target='downloader',
            event='stoptask',
            data=self.name,
        )
