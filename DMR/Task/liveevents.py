import logging
import os

from DMR.LiveAPI import LiveAPI
from .baseevents import BaseEvents
from ..utils import *
from ..utils.merge_mp4 import *
from pathlib import Path
from DMR.utils.bark_notifier  import bark_notify_url

logger = logging.getLogger(__name__)

def get_bvid_history_file(src_path):
    """获取统一的 BVID 历史文件路径"""
    return Path(src_path) / "bvid_history.json"

def read_monthly_bvids(src_path, stime, account=None):
    """
    根据 stime 读取该月份唯一的 BVID。
    返回: str (BVID) 或 None
    """
    if isinstance(stime, datetime):
        dt = stime
    elif isinstance(stime, (int, float)):
        dt = datetime.fromtimestamp(stime)
    else:
        dt = datetime.now()

    month_key = f"{dt.year}-{dt.month:02d}"
    if account:
        month_key = f"{month_key}_{account}"
    file_path = get_bvid_history_file(src_path)

    if file_path.exists():
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                full_history = json.load(f)
                # 直接返回该月份对应的 BVID 字符串
                return full_history.get(month_key)
        except Exception as e:
            print(f"读取 BVID 历史文件失败: {e}")
    return None

def write_monthly_bvid(src_path, stime, bvid, account=None):
    """
    更新 BVID 到统一的 JSON 文件。
    格式: {"2026-03": "bvid1", "2026-04": "bvid2"}
    """
    if isinstance(stime, datetime):
        dt = stime
    elif isinstance(stime, (int, float)):
        dt = datetime.fromtimestamp(stime)
    else:
        dt = datetime.now()

    month_key = f"{dt.year}-{dt.month:02d}"
    if account:
        month_key = f"{month_key}_{account}"
    file_path = get_bvid_history_file(src_path)

    full_history = {}
    if file_path.exists():
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                full_history = json.load(f)
        except Exception:
            full_history = {}

    # 每次上传结束都会更新该月份的 BVID（如果是追加模式，bvid 本身就不变）
    full_history[month_key] = bvid

    try:
        if not file_path.parent.exists():
            file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(full_history, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"写入 BVID 历史文件失败: {e}")

class LiveEvents(BaseEvents): # 被 class ReplayTask()初始化
    def __init__(self, name, config):
        super().__init__(name, config)
        self.state_dict = {}
        self.ended_dict = {}
        self.live_status= {} # zhixin新增
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
            "rendered_cover_names": set(),
            'is_live_end': False,
            'gift_stat':{
                    "total_revenue" : "未知",
                    "total_gifters" : "未知",
                    "top_ranking"   : "未知",
                    }
        }
        self.src_path        = Path(str(self.config['download_args']['output_dir']))
        self.dmvideo_path    = Path(str(self.config['download_args']['output_dir']) + '（弹幕版）')
        self.transcode_path  = Path(str(self.config['download_args']['output_dir']) + '（转码后）')
        self.check_render_cover(group_id)
        bark_args = self.config['common_event_args'].get("bark_args", {})
        # 兼容旧配置：bark_notify: True 且无 bark_args 时按默认值处理
        bark_enabled = bark_args.get("is_bark", self.config['common_event_args'].get("bark_notify", False))
        if bark_enabled:
            bark_sound = bark_args.get("sound", "birdsong")
            bark_notify_url(message.url, sound=bark_sound)

    def check_render_cover(self, group_id, sync=False):
        rendered = self.live_status[group_id]["rendered_cover_names"]
        upload_args = self.config.get('upload_args', {})

        from DMR.utils.render_with_manimgl import rendercover_with_manimgl
        import threading
        _now  = datetime.now()
        _year = f"{_now.year}"

        pending = []
        for upload_arg_list in upload_args.values():
            for arg in upload_arg_list:
                ca = arg.get('cover_args')
                if not ca or not ca.get('is_render_cover'):
                    continue
                _name = ca.get("name")
                _account = arg.get("account")
                if not _name or not _account:
                    continue
                _key = (_name, _account)
                if _key in rendered:
                    continue
                _time_template = ca.get("time_template", "{NOW.MONTH}月{NOW.DAY}日")
                _time = replace_keywords(str(_time_template), {'now': _now})
                _color = ca.get("name_color","#111111")
                pending.append((_name, _time, _color, _year, _account, f"cover_{_name}_{_account}.png"))
                rendered.add(_key)

        if not pending:
            return

        output_dir = str(self.src_path)
        def render_all():
            for (_name, _time, _color, _year, _account, filename) in pending:
                try:
                    rendercover_with_manimgl(_name,_time,_color,_year,
                                            output_dir=output_dir,
                                            output_filename=filename)
                    self.logger.info(f"封面生成成功  name:{_name}  account:{_account}  time:{_time}  year:{_year}")
                except Exception as e:
                    self.logger.exception(f"封面生成失败 name:{_name},account:{_account},time:{_time},year:{_year},error:{e}")

        if sync:
            render_all()
        else:
            threading.Thread(target=render_all, daemon=True).start()

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
        group_id = message.data
        if group_id is None:
            return

        self.live_status[group_id]['is_live_end'] = True

        # --- 第一步：标记结束 ---
        if group_id in self.state_dict:
            self.ended_dict[group_id] = time.time()
        else:
            self.logger.debug(f'No such group:{group_id}.')


        # --- 第二步：解析并保存礼物统计（必须在合并前！） ---
        if hasattr(message, "gift_stat") and message["gift_stat"]:
            raw_stat = message["gift_stat"]

            # 1. 提取名字和金额，并格式化为 "名字(金额)" (Format: Name(Value))
            # item["total_value"] 是 generate_gift_statistics 生成的数字
            names_list = [f"{item['name']}({item['total_value']})" for item in raw_stat.get("top_ranking", [])]

            # 2. 使用逗号连接 (Join with comma)
            names_str = ",".join(names_list)

            # 3. 存储到 live_status
            self.live_status[group_id]["gift_stat"] = {
                "total_revenue": raw_stat.get("total_revenue", 0),
                "total_gifters": raw_stat.get("total_gifters", 0),
                "top_ranking": names_str  # 结果示例: "悲伤小猫馄饨(20.7)，似冬(10.8)，放飞气球树(5.7)"
            }

        # --- 第三步：触发合并、上传 ---
        self.check_for_merge(group_id)

        ret_msgs = []
        if self.config['common_event_args'].get('auto_upload'):
            upload_msgs = self._check_for_upload(str(group_id))
            ret_msgs += upload_msgs

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
            stime = time.time() # 兜底逻辑

        def patch_arg_with_monthly_bvid(upload_arg_dict):
            """内部辅助：如果配置了自动追加，则注入 base_bvid (Inject Base BVID)"""
            if upload_arg_dict.get('auto_append_monthly'):
                account = upload_arg_dict.get('account')
                target_bvid = read_monthly_bvids(self.src_path, stime, account=account)
                if target_bvid:
                    upload_arg_dict['base_bvid'] = target_bvid
                    self.logger.info(f"[账号{account}] 追加模式：检测到当月已有稿件 {target_bvid}，将追加至该稿件")
                else:
                    # 如果没找到，留空让后续逻辑创建新稿件
                    upload_arg_dict['base_bvid'] = ""
                    self.logger.info(f"[账号{account}] 追加模式：当月暂无稿件，将创建新稿件")
            return upload_arg_dict

        def resolve_cover(arg, video_file=None):
            """内部辅助：根据 cover_args 自动填充 cover 路径"""
            ca = arg.get('cover_args')
            if not ca:
                return arg
            arg = dict(arg)
            if ca.get('is_render_cover'):
                _name = ca.get('name', '未知主播')
                _account = arg.get('account', '')
                arg['cover'] = str(self.src_path / f"cover_{_name}_{_account}.png")
            elif ca.get('is_extract_frame') and video_file:
                try:
                    from DMR.Uploader.acfun import extract_best_frame
                    arg['cover'] = extract_best_frame(
                        video_file.path,
                        output_dir=str(self.src_path),
                        sample_count=ca.get('sample_count', 10),
                    )
                except Exception as e:
                    self.logger.warning(f'封面自动提取失败: {e}')
            return arg
        
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
                                    'args': patch_arg_with_monthly_bvid(resolve_cover(arg, info['file'])), # 这里的arg会传入biliwebapi或biliuprs里进行初始化，和upload函数
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
                                        'args': patch_arg_with_monthly_bvid(resolve_cover(arg, up_videos[0])), # 这里的arg会传入biliwebapi或biliuprs里进行初始化，和upload函数
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
                                'args': patch_arg_with_monthly_bvid(resolve_cover(arg, all_files[0])), # 这里的arg会传入biliwebapi或biliuprs里进行初始化，和upload函数
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

        ret_msgs = []
        if self.config['common_event_args'].get('auto_upload'):
            upload_msgs = self._check_for_upload(video.group_id)
            ret_msgs += upload_msgs

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

        # --- 新增：记录 BVID 到当月历史文件 ---
        if target_group_id:
            bvid = message.data.get("bvid")
            upload_config = message.data.get("config", {}).get('args', {})
            base_bvid = upload_config.get('base_bvid')
            account = upload_config.get('account')
            if bvid and upload_config.get('auto_append_monthly'):
                stime, _ = read_last_complete_session(self.src_path)
                try:
                    write_monthly_bvid(self.src_path, stime, bvid, account=account)
                    self.logger.info(f"已登记{stime.month}月 账号{account} bvid:{bvid}")
                except Exception as e:
                    self.logger.error(f"登记{stime.month}月 账号{account} bvid失败: {e}")
            # 判断是否加入合集
            if bvid and bvid != base_bvid:
                self.check_add_to_list(target_group_id,bvid,upload_config)
        # ------------------------------------

        ret_msgs = []
        if self.config['common_event_args'].get('auto_clean') and target_group_id:
            clean_msgs = self._check_for_clean(target_group_id)
            ret_msgs += clean_msgs

        self._free_state_memory()


        return ret_msgs

    def check_add_to_list(self,target_group_id,bvid,config):
        if target_group_id not in self.live_status:
            self.logger.info(f"{self.name}:target_group_id不在live_status里\ntarget_group_id:{target_group_id}\nself.live_status:{self.live_status}")
            return

        need_add_to_list = config.get("add_to_list",False)
        account          = config.get('account',None)
        sectionId        = config.get('sectionId',None)

        if need_add_to_list:
            try:
                add_to_list(bvid,sectionId,account)
            except Exception as e:
                self.logger.error(e)

    def check_for_merge(self, group_id):
        # is_merge / merge_type / 音量相关配置
        merge_cfg          = self.config['common_event_args'].get("merge_args", {}) or {}
        is_merge           = merge_cfg.get("is_merge")
        merge_type         = merge_cfg.get("merge_type")  # e.g. ['src_video', 'dm_video']
        is_amplify         = merge_cfg.get("is_amplify", True)
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
            dst = target_slot.get(vt, vt)  # 默认映射自己
            gift_stat  = self.live_status[group_id]["gift_stat"]
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
                total_revenue = gift_stat.get("total_revenue",""),
                total_gifters = gift_stat.get("total_gifters",""),
                top_ranking   = gift_stat.get("top_ranking",""),
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
            self.logger.debug("check_for_merge: 所有 merge_type 都未合并成功，结束")
            return

        # 至少有一个 vt 合并成功：追加新的 state
        self.state_dict[group_id].append(new_state)

    def _check_for_clean(self, group_id=None):
        # 修改后的清理逻辑：
        # 只在上传完成后检查是否清理
        # 根据文件类型进行清理dm_video，src_video，src_video_pre or all
        # self._log_state(prefix="checkforcleanbefore:",level=logging.DEBUG)
        ret_msgs = []
        clean_args = self.config['clean_args']
        for group_id, video_states in self.state_dict.items():
            for idx, video_state in enumerate(video_states):
                for vtype, info in video_state.items():
                    # if info['status'] != 'uploaded':
                    #     continue
                    for clean_file_types, clean_arg in clean_args.items():
                        # 判断当前视频是否需要清理
                        if vtype in clean_file_types.split('+') or clean_file_types == 'all':
                            for arg in clean_arg:
                                if file := info['file']:
                                    self.state_dict[group_id][idx][vtype]['status'] = 'cleaned'
                                    self.logger.debug(f"file:{file}")
                                    clean_msg = PipeMessage(
                                        source=self.name,
                                        target='cleaner',
                                        event='newtask',
                                        request_id=uuid(),
                                        data={
                                            'taskname': self.name,
                                            'files': [file],
                                            'method': arg['method'],
                                            'delay': arg['delay'],
                                            'args': arg,
                                        }
                                    )
                                    ret_msgs.append(clean_msg)

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
                    if st is None:
                        continue
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
