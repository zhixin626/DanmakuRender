import logging
import os
import re
from pathlib import Path

from DMR.LiveAPI import LiveAPI
from DMR.utils.bark_notifier import bark_notify_url
from DMR.utils.extract_frame import extract_best_frame
from DMR.utils.render_with_manimgl import rendercover_with_manimgl
from .baseevents import BaseEvents
from ..utils import *
from ..utils.merge_mp4 import *
from ..utils.gifts_utils import generate_gift_statistics

logger = logging.getLogger(__name__)

def get_bvid_history_file(src_path):
    """获取统一的 BVID 历史文件路径"""
    return Path(src_path) / "bvid_history.json"

def _make_period_key(stime, period):
    """根据 period ('monthly'/'daily') 和 stime 生成 bvid_history.json 的 key。"""
    if isinstance(stime, datetime):
        dt = stime
    elif isinstance(stime, (int, float)):
        dt = datetime.fromtimestamp(stime)
    else:
        dt = datetime.now()

    if period == 'daily':
        return f"{dt.year}-{dt.month:02d}-{dt.day:02d}"
    else:  # monthly（默认）
        return f"{dt.year}-{dt.month:02d}"

def read_period_bvid(src_path, stime, period='monthly', account=None):
    """
    根据 stime 和 period 读取对应周期的 BVID。
    返回: str (BVID) 或 None
    """
    key = _make_period_key(stime, period)
    if account:
        key = f"{key}_{account}"
    file_path = get_bvid_history_file(src_path)

    if file_path.exists():
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                full_history = json.load(f)
                return full_history.get(key)
        except Exception as e:
            print(f"读取 BVID 历史文件失败: {e}")
    return None

def write_period_bvid(src_path, stime, bvid, period='monthly', account=None):
    """
    更新 BVID 到统一的 JSON 文件。
    monthly 格式: {"2026-03": "bvid1"}，daily 格式: {"2026-03-05": "bvid1"}
    """
    key = _make_period_key(stime, period)
    if account:
        key = f"{key}_{account}"
    file_path = get_bvid_history_file(src_path)

    full_history = {}
    if file_path.exists():
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                full_history = json.load(f)
        except Exception:
            full_history = {}

    full_history[key] = bvid

    try:
        if not file_path.parent.exists():
            file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(full_history, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"写入 BVID 历史文件失败: {e}")

# 向后兼容旧函数名
def read_monthly_bvids(src_path, stime, account=None):
    return read_period_bvid(src_path, stime, period='monthly', account=account)

def write_monthly_bvid(src_path, stime, bvid, account=None):
    return write_period_bvid(src_path, stime, bvid, period='monthly', account=account)

class LiveEvents(BaseEvents): # 被 class ReplayTask()初始化
    def __init__(self, name, config):
        super().__init__(name, config)
        self.state_dict = {}
        self.ended_dict = {}
        self.live_status= {} # zhixin新增
        self.merged_videos = []  # 合并成功的视频记录（供 WebUI「已合并的视频」展示，最多保留最近若干条）
        self.logger = logging.getLogger(__name__)

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

    def defaultEvent(self, message:PipeMessage): # 默认事件就是在event_dict里找不到的情况下用 如 liveerror
        self.logger.info(f'{self.name}: {message.msg}')

    def onLiveStart(self, message:PipeMessage):
        group_id = message.data
        self.live_status[group_id] = {
            "is_live_end"   : False,
            # 会话级字段，由 _backfill_video_info 统一回填到所有 VideoInfo
            "gifts_revenue" : "",
            "num_of_gifters": "",
            "sc_revenue"    : "",
            "num_of_sc"     : "",
            "member_revenue": "",
            "member_info"   : "",
            "total_revenue" : "",
            "top_ranking"   : "",
            "stime"         : None,
            "etime"         : None,
            "totaltime"     : "",
        }
        self.src_path        = Path(str(self.config['download_args']['output_dir']))
        self.dmvideo_path    = Path(str(self.config['download_args']['output_dir']) + '（弹幕版）')
        self.transcode_path  = Path(str(self.config['download_args']['output_dir']) + '（转码后）')
        self.bark_notify(message.url)

    def patch_arg_bvid(self, arg, stime):
        period = arg.get('auto_append_period')
        if period in ('monthly', 'daily'):
            account = arg.get('account')
            target_bvid = read_period_bvid(self.src_path, stime, period=period, account=account)
            if target_bvid:
                arg['base_bvid'] = target_bvid
                self.logger.info(f"[账号{account}] 追加模式({period})：检测到已有稿件 {target_bvid}，将追加至该稿件")
            else:
                arg['base_bvid'] = ""
                self.logger.info(f"[账号{account}] 追加模式({period})：暂无稿件，将创建新稿件")
        return arg

    def patch_arg_cover(self, arg, video_file=None, stime=None):
        ca = arg.get('cover_args')
        if not ca:
            return arg

        def do_extract():
            if not video_file:
                return None
            try:
                return extract_best_frame(
                    video_file.path,
                    output_dir=str(Path(video_file.path).parent),
                    sample_count=ca.get('sample_count', 10),
                    ratio=ca.get('ratio', '16/9'),
                )
            except Exception as e:
                self.logger.warning(f'封面帧提取失败: {e}')
                return None

        def do_render(image_path=None):
            _name = ca.get('name', '未知主播')
            _now = datetime.now()
            account = arg.get('account', '')
            safe_account = re.sub(r'[\\/:*?"<>|]', '_', str(account))
            output_filename = f'cover_{safe_account}.png' if safe_account else None
            try:
                return rendercover_with_manimgl(
                    _name,
                    replace_keywords(str(ca.get('time_template', '{NOW.MONTH}月{NOW.DAY}日')), {'now': _now, 'stime': stime}),
                    ca.get('name_color', '#111111'),
                    str(_now.year),
                    output_dir=str(self.src_path),
                    image_path=image_path,
                    output_filename=output_filename,
                )
            except Exception as e:
                self.logger.warning(f'封面渲染失败: {e}')
                return None

        arg = dict(arg)
        if ca.get('is_render_cover'):
            image_path = do_extract() if ca.get('is_extract_frame') else None
            cover = do_render(image_path)
        elif ca.get('is_extract_frame'):
            cover = do_extract()
        else:
            return arg

        if cover:
            arg['cover'] = cover
        return arg

    def bark_notify(self, url):
        bark_args = self.config.get("bark_args", {})
        if not bark_args.get("is_bark"):
            return
        bark_notify_url(url, sound=bark_args.get("sound", "birdsong"))

    def _log_state(self, which: str = "all", prefix: str = "", level: int = logging.INFO):
        """
        打印内部状态快照（可读、结构稳定）：
          - state: 保持你原来的 state_dict 结构：gid -> [ {vt: {status, wait, file}} ... ]
          - ended: gid -> ended_timestamp
          - live : gid -> {is_live_end, bvid, flags...}
          - all  : 同时输出三者（顶层三个 key：state/ended/live）
        """
        def _file_repr(f):
            if not f:
                return None
            return getattr(f, "path", str(f))

        def _state_snap():
            snap = {}
            for gid, vss in self.state_dict.items():
                snap[gid] = []
                for vs in vss:
                    row = {}
                    for vt, info in vs.items():
                        f = info.get("file")
                        row[vt] = {
                            "status": info.get("status"),
                            "wait": list(info.get("wait") or []),
                            "file": _file_repr(f),
                        }
                    snap[gid].append(row)
            return snap

        def _ended_snap():
            # ended_dict: gid -> timestamp(float)
            return dict(self.ended_dict)

        def _live_snap():
            # live_status: gid -> dict(flags...)
            snap = {}
            for gid, s in self.live_status.items():
                # 复制一份，避免日志里出现引用导致后续被改动看不懂
                snap[gid] = dict(s) if isinstance(s, dict) else s
            return snap

        which = (which or "state").lower()
        payload = None

        if which in ("state", "statedict", "state_dict"):
            payload = _state_snap()
        elif which in ("ended", "endeddict", "ended_dict"):
            payload = _ended_snap()
        elif which in ("live", "livestatus", "live_status"):
            payload = _live_snap()
        elif which in ("all", "*"):
            payload = {
                "state": _state_snap(),
                "ended": _ended_snap(),
                "live": _live_snap(),
            }
        else:
            payload = {"error": f"unknown which={which}", "supported": ["state", "ended", "live", "all"]}

        # prefix 方便你定位是哪个阶段打的点
        if prefix:
            self.logger.log(level, f"{prefix}: {payload}")
        else:
            self.logger.log(level, payload)

        return payload


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
            # 配置 dmrender.emoji=true 时改用彩色 emoji 渲染引擎
            render_mode = 'emoji_dmrender' if render_args.get('emoji') else 'dmrender'
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
                    'mode': render_mode,
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
        group_id = message.data
        if group_id is None:
            return

        self.live_status[group_id]['is_live_end'] = True

        # --- 第一步：标记结束 ---
        if group_id in self.state_dict:
            self.ended_dict[group_id] = time.time()
        else:
            self.logger.debug(f'No such group:{group_id}.')


        # --- 第二步：收集会话数据写入 live_status ---
        self._collect_session_data(group_id)

        # --- 第三步：合并，然后统一回填所有 VideoInfo ---
        self.check_for_merge(group_id)
        self._backfill_video_info(group_id)

        ret_msgs = []
        if self.config['common_event_args'].get('auto_upload'):
            upload_msgs = self._check_for_upload(str(group_id))
            ret_msgs += upload_msgs

        if self.config['common_event_args'].get('auto_clean'):
            clean_msgs = self._check_for_clean(str(group_id), trigger='liveend')
            ret_msgs += clean_msgs

        self._free_state_memory()

        return ret_msgs

    def _check_for_upload(self, group_id:str, _idx:int=None):
        # self._log_state("Before _check_for_upload")
        ret_msgs = []
        if not self.state_dict.get(group_id):
            return ret_msgs

        try:
            # 假设 read_last_complete_session 已从外部导入
            stime, etime = read_last_complete_session(self.src_path)
        except:
            stime = datetime.now() # 兜底逻辑


        upload_args = self.config['upload_args']
        upload_together =self.config["common_event_args"].get("upload_together",False) #zhixin

        # --- 路径一：实时上传 (Realtime) ---
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
                            if not arg.get('realtime'): continue
                            if info['file'].duration < arg.get('min_length', 0): continue

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
                                    'args': self.patch_arg_bvid(self.patch_arg_cover(arg, info['file'], stime), stime), # 这里的arg会传入biliwebapi或biliuprs里进行初始化，和upload函数
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
            video_types = list(self.state_dict[group_id][-1].keys())# 遍历所有视频类型

            # --- 路径二：非实时上传 (Standard Non-Realtime) ---
            if not upload_together: # zhixin: 原始逻辑
                for vtype in video_types:
                    # 检查是否全部准备上传
                    videos = []
                    for idx, video_state in enumerate(self.state_dict[group_id]):
                        if video_state[vtype]['status'] == 'ready':
                            videos.append(video_state[vtype]['file'])
                        elif video_state[vtype]['status'] in ('merged', None):
                            # 注意这里是配合check_for_merge
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
                                if arg.get('realtime'): continue

                                up_videos = [video for video in videos if video.duration >= arg.get('min_length', 0)]
                                if not up_videos:
                                    self.logger.info(f'{self.name}: 视频时长不足 {arg.get("min_length", 0)} 秒，跳过上传。')
                                    continue
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
                                        'args': self.patch_arg_bvid(self.patch_arg_cover(arg, up_videos[0], stime), stime), # 这里的arg会传入biliwebapi或biliuprs里进行初始化，和upload函数
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

            # --- 路径三：非实时聚合上传 (Upload Together) ---
            else: # zhixin新逻辑：按 upload_file_types 聚合，把多个 vtype 的 files 合并成一次 task ======
                for upload_file_types, upload_arg in upload_args.items():
                    vtypes = upload_file_types.split('+')

                    # 只处理非 realtime
                    for upid, arg in enumerate(upload_arg):
                        if arg.get('realtime'): continue

                        all_files = []
                        involved_vtypes = []
                        ok = True

                        for vtype in vtypes:
                            # 不存在的 vtype 直接视为不满足
                            if vtype not in video_types:
                                ok = False
                                break

                            videos = []
                            for idx, video_state in enumerate(self.state_dict[group_id]):
                                st = video_state[vtype]['status']
                                if st == 'ready':
                                    videos.append(video_state[vtype]['file'])
                                elif st in ('merged', None): # 注意这里是配合check_for_merge
                                    continue
                                else:
                                    ok = False
                                    break
                            if not ok:
                                break

                            # min_length 过滤
                            up_videos = [v for v in videos if v.duration >= arg.get('min_length', 0)]
                            if not up_videos:
                                ok = False
                                break

                            all_files.extend(up_videos)
                            involved_vtypes.append(vtype)

                        if not ok or not all_files:
                            continue

                        first = all_files[0]
                        upload_group_id = first.upload_group_id if hasattr(first, 'upload_group_id') else group_id

                        upload_msg = PipeMessage(
                            source=self.name,
                            target='uploader',
                            event='newtask',
                            request_id=uuid(),
                            data={
                                'taskname': self.name,
                                'files': all_files,   # ⭐ 多个 vtype 合在一起
                                'engine': arg['engine'],
                                'stateless': True,
                                'upload_group': upload_group_id + '_' + upload_file_types + '_' + str(upid),
                                'args': self.patch_arg_bvid(self.patch_arg_cover(arg, all_files[0], stime), stime), # 这里的arg会传入biliwebapi或biliuprs里进行初始化，和upload函数
                            }
                        )

                        # 标记 uploading：涉及到的所有 vtype 都标
                        for idx, _ in enumerate(self.state_dict[group_id]):
                            for vtype in involved_vtypes:
                                if self.state_dict[group_id][idx][vtype]['status'] in ('ready', 'merged'):
                                    self.state_dict[group_id][idx][vtype]['status'] = 'uploading'
                                    self.state_dict[group_id][idx][vtype]['wait'].append(upload_msg.request_id)

                        ret_msgs.append(upload_msg)

        return ret_msgs
    
    def onRenderEnd(self, message:PipeMessage):
        # self._log_state(prefix="onRenderEnd_start",level=logging.DEBUG)
        self.logger.info(f'{self.name}: {message.msg}.')

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
        
        self.check_for_merge(video.group_id)
        self._backfill_video_info(video.group_id)

        ret_msgs = []
        # 字幕中间文件（txt/ass）渲染完即用完，单独发清理消息（与 auto_clean 无关）
        ret_msgs += self._subtitle_clean_msgs(message.data.get('config'))

        if self.config['common_event_args'].get('auto_upload'):
            upload_msgs = self._check_for_upload(video.group_id)
            ret_msgs += upload_msgs

        if self.config['common_event_args'].get('auto_clean'):
            clean_msgs = self._check_for_clean(video.group_id, trigger='render')
            ret_msgs += clean_msgs

        self._free_state_memory()
        return ret_msgs

    def onUploadEnd(self, message:PipeMessage):
        self.logger.info(f'{self.name}: {message.msg}.')
        # self.logger.debug(f"BeforeonUploadEnd:{self.name}: {message}.")

        target_group_id = None
        request_id = message.request_id
        # 将状态信息中request_id对应的等待移除
        for group_id, video_states in self.state_dict.items():
            for idx, video_state in enumerate(video_states):
                for vtype, info in video_state.items():
                    if request_id in info['wait']:
                        target_group_id = group_id
                        self.state_dict[group_id][idx][vtype]['wait'].remove(request_id)
                        if len(self.state_dict[group_id][idx][vtype]['wait']) == 0:
                            self.state_dict[group_id][idx][vtype]['status'] = 'uploaded'

        # --- 新增：记录 BVID 到历史文件 ---
        if target_group_id:
            bvid = message.data.get("bvid")
            upload_config = message.data.get("config", {}).get('args', {})
            base_bvid = upload_config.get('base_bvid')
            account = upload_config.get('account')
            period = upload_config.get('auto_append_period')
            if bvid and period in ('monthly', 'daily'):
                stime, _ = read_last_complete_session(self.src_path)
                try:
                    write_period_bvid(self.src_path, stime, bvid, period=period, account=account)
                    period_label = f"{stime.year}-{stime.month:02d}-{stime.day:02d}" if period == 'daily' else f"{stime.year}-{stime.month:02d}"
                    self.logger.info(f"已登记 {period_label} 账号{account} bvid:{bvid}")
                except Exception as e:
                    self.logger.error(f"登记 账号{account} bvid失败: {e}")
            # 判断是否加入合集
            if bvid and bvid != base_bvid:
                self.check_add_to_list(target_group_id,bvid,upload_config)
        # ------------------------------------

        ret_msgs = []
        if self.config['common_event_args'].get('auto_clean') and target_group_id:
            clean_msgs = self._check_for_clean(target_group_id, trigger='upload')
            ret_msgs += clean_msgs

        self._free_state_memory()


        return ret_msgs

    def check_add_to_list(self,target_group_id,bvid,config):
        if target_group_id not in self.live_status:
            self.logger.info(f"{self.name}:target_group_id不在live_status里\ntarget_group_id:{target_group_id}\nself.live_status:{self.live_status}")
            return

        need_add_to_list = config.get("add_to_list",False)
        account          = config.get('account',None)
        season_id        = config.get('season_id',None)

        if need_add_to_list:
            try:
                add_to_list(bvid,season_id,account)
            except Exception as e:
                self.logger.error(e)

    def check_for_merge(self, group_id):
        # is_merge / merge_type / 音量相关配置
        merge_cfg          = self.config.get("merge_args", {}) or {}
        is_merge           = merge_cfg.get("is_merge")
        merge_type         = merge_cfg.get("merge_type")  # e.g. ['src_video', 'dm_video']
        is_amplify         = merge_cfg.get("is_amplify", False)
        extra_gain_db      = merge_cfg.get("extra_gain_db", 0)
        file_name_template = merge_cfg.get("file_name_template", "{stime.month}月{stime.day}日")

        # 基础防御：不开启合并 / 没有这个 group / merge_type 为空 → 直接退出
        if (not is_merge) or (group_id not in self.ended_dict) or (not merge_type):
            return

        # 所有 seg 的起止时间（你原来的逻辑）
        stime,etime=read_last_complete_session(self.src_path)
        totaltime=format_duration(stime,etime)
        final_file_name=file_name_template.format(stime=stime)

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
                if entry['status'] is None:
                    # 这里避免先合并了某一种然后退出check_for_merge
                    # 然后又进入一次check_for_merge的情况
                    continue
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
                # 安全重命名
                output_path_obj = Path(output_path)
                dst_path = str(output_path_obj.parent / f"{final_file_name}{output_path_obj.suffix}")
                renamed = rename_safe(str(output_path), dst_path)
                output_path = renamed if renamed else output_path
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
            # 会话级字段（gifts_revenue / stime 等）由 _backfill_video_info 统一回填，不在此设置
            dst = target_slot.get(vt, vt)
            newvideo = VideoInfo(
                path      = str(output_path),
                dtype     = dst,
                file_id   = uuid(),
                size      = os.path.getsize(output_path),
                ctime     = datetime.now(),
                stime     = stime,
                etime     = etime,
                totaltime = totaltime,
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

            # 记录合并产物，供 WebUI「已合并的视频」展示
            self.merged_videos.append({
                'output': str(output_path),
                'type': vt,
                'segments': len(paths),
                'time': datetime.now(),
            })
            if len(self.merged_videos) > 200:
                self.merged_videos = self.merged_videos[-200:]

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
            self.logger.debug("check_for_merge: 所有 merge_type 都未合并成功，结束")
            return

        # 至少有一个 vt 合并成功：追加新的 state
        self.state_dict[group_id].append(new_state)

    def _collect_session_data(self, group_id: str):
        """
        收集本场直播的会话级数据，写入 live_status[group_id]：
          - 礼物/SC 统计（gifts_revenue、sc_revenue 等）
          - 开播/下播/总时长（stime、etime、totaltime）
        onLiveEnd 调用，_backfill_video_info 之前执行。
        """
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
                    self.live_status[group_id].update({
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
            self.live_status[group_id].update({
                "stime"    : stime,
                "etime"    : etime,
                "totaltime": format_duration(stime, etime),
            })
        except Exception as e:
            self.logger.warning(f"获取开播/下播时间失败: {e}")

    def _backfill_video_info(self, group_id: str):
        """
        将 live_status 中的会话级字段统一回填到该 group 所有 VideoInfo。
        在 check_for_merge 之后调用，确保合并生成的新 VideoInfo 也能被覆盖。
        """
        status = self.live_status.get(group_id, {})
        fields = [
            'gifts_revenue', 'num_of_gifters',
            'sc_revenue', 'num_of_sc',
            'member_revenue', 'member_info',
            'total_revenue', 'top_ranking',
            'stime', 'etime', 'totaltime',
        ]
        for video_state in self.state_dict.get(group_id, []):
            for info in video_state.values():
                if info.get('file'):
                    for field in fields:
                        val = status.get(field)
                        if val is not None and val != "":
                            setattr(info['file'], field, val)

    def _subtitle_clean_msgs(self, render_config):
        """字幕中间文件（ASR 的 txt 与 (字幕).ass）渲染完即用完，单独发清理消息走清理管线。
        清理策略读 subtitle 配置：clean(默认True是否清理)、clean_method(默认send2trash)、clean_delay(默认0)。"""
        cfg = render_config or {}
        if cfg.get('mode') != 'dmrender':
            return []
        sub = (cfg.get('args') or {}).get('subtitle') or {}
        if not sub.get('enable') or not sub.get('clean', True):
            return []
        src = cfg.get('video') or {}
        src_path = src.get('path') if hasattr(src, 'get') else None
        if not src_path:
            return []
        from DMR.utils.dataclass import FileInfo
        stem = os.path.splitext(src_path)[0]
        files = [FileInfo(path=p) for p in (stem + '.txt', stem + '(字幕).ass') if os.path.exists(p)]
        if not files:
            return []
        return [PipeMessage(
            source=self.name, target='cleaner', event='newtask', request_id=uuid(),
            data={
                'taskname': self.name,
                'files'   : files,
                'method'  : sub.get('clean_method', 'send2trash'),
                'delay'   : sub.get('clean_delay', 0),
                'args'    : {},
            }
        )]

    def _check_for_clean(self, group_id: str, trigger: str = 'upload'):
        """
        检查并生成清理任务。
        trigger: 触发阶段，与 clean_args 里各条目的 trigger 字段匹配。
          'upload'  — 上传完成后触发（默认，向下兼容）
          'render'  — 渲染完成后触发
          'liveend' — 直播/录制结束时触发
        只处理 trigger 匹配的 clean_arg 条目，不填则默认 'upload'。
        """
        ret_msgs = []
        clean_args = self.config.get('clean_args') or {}

        video_states = self.state_dict.get(group_id)
        if not video_states:
            return ret_msgs

        for idx, video_state in enumerate(video_states):
            for vtype, info in video_state.items():
                for clean_file_types, clean_arg in clean_args.items():
                    if vtype not in clean_file_types.split('+') and clean_file_types != 'all':
                        continue
                    for arg in clean_arg:
                        # trigger 不填默认 upload，向下兼容
                        if arg.get('trigger', 'upload') != trigger:
                            continue
                        if file := info['file']:
                            self.state_dict[group_id][idx][vtype]['status'] = 'cleaned'
                            self.logger.debug(f'清理触发（{trigger}）: {file}')
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
                            ret_msgs.append(clean_msg)

        self._free_state_memory()
        return ret_msgs
    
    def _free_state_memory(self):
        # self._log_state(prefix="_free_state_memory_start:",level=logging.DEBUG)

        final_status =  ['ready']
        if self.config['common_event_args'].get('auto_upload'):
            final_status.append('uploaded')
        if self.config['common_event_args'].get('auto_clean'):
            final_status.append('cleaned')

        for group_id in list(self.ended_dict.keys()):
            video_states = self.state_dict.get(group_id)
            if not video_states:
                # state_dict 已经没了，就把 ended/live_status 一并清掉
                self.ended_dict.pop(group_id, None)
                self.live_status.pop(group_id, None)
                continue

            need_free = True
            for video_state in video_states:
                for info in video_state.values():
                    st = info.get('status')
                    if st is None or st == 'merged':
                        continue  # None=未参与, merged=已被合并消费，均视为终态跳过
                    # merging 这种中间态绝对不能释放
                    if st not in final_status:
                        need_free = False
                        break
                    # zhixin新增：wait 不为空说明还有上传任务在途，不能释放
                    if info.get('wait'):
                        need_free = False
                        break
                if not need_free:
                    break

            if need_free:
                self.logger.info(f'视频组{group_id}处理完成，视频信息已被释放.')
                self.ended_dict.pop(group_id, None)
                self.state_dict.pop(group_id, None)
                self.live_status.pop(group_id, None)

        # 超时释放：也要用 pop(..., None) 防 KeyError
        for group_id in list(self.ended_dict.keys()):
            if time.time() - self.ended_dict[group_id] > 72 * 3600:
                self.logger.info(f'视频组{group_id}处理超时，视频信息将被释放.')
                self.ended_dict.pop(group_id, None)
                self.state_dict.pop(group_id, None)
                self.live_status.pop(group_id, None)

        # self._log_state(prefix="_free_state_memory_end:",level=logging.DEBUG)

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
