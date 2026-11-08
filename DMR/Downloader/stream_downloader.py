import logging
import os
import queue
import threading
import time
import re

from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError
from os.path import join,exists,splitext
from datetime import datetime
from DMR.Downloader.Danmaku import DanmakuDownloader
from DMR.LiveAPI import *
from DMR.utils import *
from pathlib import Path
from DMR.utils.console import COLORS, pad_disp
from DMR.utils.seconds_until import is_now_in_time_ranges,get_check_interval,is_passed_time_point

from enum import Enum

class StreamState(str, Enum):
    OFFLINE = "offline"                  # 稳态：不在播
    LIVE = "live"                        # 稳态：直播中（允许录）
    REPLAY = "replay"                    # 稳态：回放/录像中（禁录）
    LIVE_END = "live_end"        # 瞬时态：直播刚结束（要发 liveend）
    REPLAY_END = "replay_end"    # 瞬时态：回放刚结束（不发 liveend，仅复位）
    LIVE_START = "live_start"
    REPLAY_START = "replay_start"
    STOPPING = "stopping"        # 稳态：刚下播,等待确认是否真的下播(stop_wait_time)

class StreamUnavailableError(RuntimeError):
    """房间报在播但拿不到直播流地址（如加密/密码房间）。
    用于区分"正常录制中途崩溃"(走快速重启)和"压根取不到流"(走慢速轮询)，避免无限空转重启。"""
    pass

class StreamDownloadTask(): # 被上层class Downloader():的new_task函数中初始化
    def __init__(self,
                 url,
                 output_dir,
                 send_queue:queue.Queue,
                 segment:int,
                 output_name=None,
                 taskname=None,
                 stream_args:dict=None,
                 danmaku_args:dict=None,
                 debug=False,
                 **kwargs
        ) -> None:
        self.taskname = taskname
        self.url = url
        self.plat, self.rid = split_url(url)
        self.liveapi = LiveAPI(url)
        self.output_dir = output_dir or './' + self.taskname
        self.output_name = output_name
        self.send_queue = send_queue
        self.logger = logging.getLogger(__name__)
        self.kwargs = kwargs
        self.debug = debug
        self.segment = segment

        # —— 视频流录制参数（stream_args）——
        stream_args = stream_args if stream_args else {}
        self.stream_args = stream_args
        self.video = stream_args.get('enable', True)
        self.engine = stream_args.get('engine') or 'auto'
        self.output_format = stream_args.get('output_format')  # 留空则在 start_once 里按实际引擎自动决定容器
        self.stop_wait_time = stream_args.get('stop_wait_time', 0)
        self.stream_option = stream_args.get('stream_option') or {}
        self.advanced_video_args = stream_args.get('advanced_args') or {}

        # —— 弹幕参数（danmaku_args）整体交给 DanmakuDownloader，这里只取本类要用的几项 ——
        danmaku_args = danmaku_args if danmaku_args else {}
        self.danmaku_args = danmaku_args
        self.danmaku = danmaku_args.get('enable', True)
        self.dm_format = 'jsonl'

        self.stoped = True
        # WebUI 只读取这些单调时钟截止点来显示倒计时；不会因此增加平台请求。
        self.next_check_deadline = None
        self.stop_confirm_deadline = None
        # OFFLINE 原先用不可中断的 time.sleep；改用 Event 后可被「立即检测」唤醒。
        self._check_wakeup = threading.Event()
        self._check_now_lock = threading.Lock()
        self._last_check_now = 0.0
        self._segment_lock = threading.RLock()
        self._segment_contexts = {}
        self._ended_segment_keys = set()
        self._dm_preopened_start_time = None
        self.segment_start_time = None
        os.makedirs(self.output_dir,exist_ok=True)
    @property
    def taskname_disp(self) -> str:
        return (
            f"{COLORS['yellow']}"
            f"{pad_disp(str(self.taskname), 15)}"
            f"{COLORS['reset']}"
        )

    def _pipeSend(self, event, msg, target=None, dtype=None, data=None, **kwargs):
        if self.send_queue:
            target = target if target else f'replay/{self.taskname}'
            msg = PipeMessage(
                source='downloader',
                target=target,
                event=event, # 在engine里if message.event == 'info':self.logger.info(message.msg)
                msg=msg,
                dtype=dtype,
                data=data,
                **kwargs,
            )
            self.send_queue.put(msg)

    @staticmethod
    def _segment_key(filename: str) -> str:
        """统一下载中临时名和完成名；StreamGears 的 .part 重命名不应产生两个段。"""
        key = os.path.abspath(str(filename))
        if key.lower().endswith(".part"):
            key = key[:-5]
        return os.path.normcase(key)

    def _reset_segment_tracking(self):
        with self._segment_lock:
            self._segment_contexts.clear()
            self._ended_segment_keys.clear()
            self._dm_preopened_start_time = None
            self.segment_start_time = None

    def segment_start_callback(self, filename: str, start_time: float):
        """登记下载引擎实际打开的视频文件，并开始对应的弹幕事件段。"""
        if not filename:
            return
        key = self._segment_key(filename)
        start_time = float(start_time)
        start_dt = datetime.fromtimestamp(start_time)

        with self._segment_lock:
            if key in self._segment_contexts:
                return
            context = {
                "filename": filename,
                "segment_id": self.segment_id,
                "start_time": start_dt,
                "room_info": self.room_info,
                "streamer_info": self.streamer_info,
            }
            self._segment_contexts[key] = context
            self._ended_segment_keys.discard(key)
            # 仅供 WebUI 和本轮录制时长显示；视频收尾必须读取自己的 context。
            self.segment_start_time = start_dt

        dm_already_open = False
        with self._segment_lock:
            if self._dm_preopened_start_time is not None:
                dm_already_open = abs(self._dm_preopened_start_time - start_time) < 0.001
                self._dm_preopened_start_time = None

        if self.danmaku and self.dmw and not dm_already_open:
            try:
                self.dmw.open_segment(start_time=start_time)
            except RuntimeError:
                # 正常连续分段必须由引擎先上报旧段结束；出现反序时不能覆盖旧段。
                with self._segment_lock:
                    self._segment_contexts.pop(key, None)
                self.logger.exception("视频段开始事件顺序异常: %s", filename)
                return

        self.logger.debug("视频段开始: %s, start_time=%.6f", filename, start_time)

    def segment_end_callback(
        self,
        filename: str,
        end_time: float,
        continuous: bool = False,
    ):
        """结束指定视频段；开始时间只从该文件自己的上下文读取。"""
        if not filename:
            return
        key = self._segment_key(filename)
        end_time = float(end_time)

        with self._segment_lock:
            if key in self._ended_segment_keys:
                return
            context = self._segment_contexts.pop(key, None)
            if context is None:
                self.logger.warning("收到未登记开始时间的视频段结束事件: %s", filename)
                return
            self._ended_segment_keys.add(key)

        # 必须先按边界关闭 JSONL。FFprobe 和重命名可能耗时，若延后关闭，
        # 边界后的事件可能越过尾部缓冲并错误落进旧段。
        old_dm_file = None
        if self.danmaku and self.dmw:
            if continuous:
                old_dm_file = self.dmw.rotate_segment(
                    next_segment_start_time=end_time,
                )
                with self._segment_lock:
                    self._dm_preopened_start_time = end_time
            else:
                old_dm_file = self.dmw.close_segment(end_time=end_time)

        if not exists(filename):
            self.logger.debug("视频段文件不存在: %s", filename)
            if old_dm_file and exists(old_dm_file):
                os.remove(old_dm_file)
            return

        start_time = context["start_time"]
        wall_duration = max(0.0, end_time - start_time.timestamp())
        # 直播 FLV 可能保留上游累计时间戳，format.duration 因而不一定是本段时长。
        # 以视频包时间线跨度作为分段媒体时长，探测失败时该函数内部仍会回退到容器时长。
        probed_duration = FFprobe.get_timeline_duration(filename)
        # 0 秒同样是无效探测结果；使用现实经过时间兜底，避免把已录制的
        # FLV 误判为短视频并连同 JSONL 一起删除。
        duration = probed_duration if probed_duration > 0 else wall_duration
        room_info = context.get("room_info") or self.room_info or {}
        streamer_info = context.get("streamer_info") or self.streamer_info

        video_info = VideoInfo(
            path=filename,
            file_id=uuid(),
            dtype='src_video',
            group_id=self.sess_id,
            segment_id=context["segment_id"],
            segment_start_time=start_time,
            size=os.path.getsize(filename),
            ctime=start_time,
            duration=duration,
            resolution=(self.width, self.height),
            title=room_info.get("title", ""),
            streamer=streamer_info,
            taskname=self.taskname,
        )

        max_fn_length = self.advanced_video_args.get('max_fn_length', 80)
        raw_filename = replace_keywords(self.output_name, video_info, replace_invalid=True)[:max_fn_length]

        newfile = join(self.output_dir, raw_filename+'.'+self.output_format)
        _file = rename_safe(filename, newfile)
        if _file:
            newfile = _file
            self.logger.debug(f'Rename video file {filename} to {newfile}.')
        else:
            newfile = filename
            self.logger.error(f'视频 {newfile} 分段失败，将使用默认名称 {filename}.')

        if self.danmaku and old_dm_file:
            newdmfile = splitext(newfile)[0]+'.'+self.dm_format
            try:
                os.replace(old_dm_file, newdmfile)
            except OSError:
                self.logger.exception("弹幕事件文件重命名失败: %s -> %s", old_dm_file, newdmfile)
                newdmfile = old_dm_file
        else:
            newdmfile = None

        video_info.path = newfile
        video_info.dm_file_id = newdmfile
        if self.advanced_video_args.get('group_id'):
            group_id = str(self.advanced_video_args['group_id'])
            group_id = replace_keywords(group_id, video_info)
            video_info.upload_group_id = group_id

        min_video_size = self.advanced_video_args.get('min_video_size')
        min_video_duration = self.advanced_video_args.get('min_video_duration')
        if (min_video_size and video_info.size < min_video_size *1024*1024) \
            or (min_video_duration and video_info.duration < min_video_duration):
            self.logger.info(f'视频 {video_info.path} 过小, 设置 {min_video_size}MB {min_video_duration}s,'
                             f'实际 {video_info.size/1024/1024:.2f}MB {video_info.duration}s')
            if exists(video_info.path):
                os.remove(video_info.path)
            if video_info.dm_file_id and exists(video_info.dm_file_id):
                os.remove(video_info.dm_file_id)
            return

        self._pipeSend(event='livesegment', msg=f'视频分段 {newfile} 录制完成.', target=f'replay/{self.taskname}', dtype='VideoInfo', data=video_info)
        new_room_info = retry_safe(self.liveapi.GetRoomInfo)
        if new_room_info:
            self.room_info = new_room_info
        self.segment_id += 1

    def start_once(self):
        self.stoped = False

        # init segment info
        self.room_info = retry_safe(self.liveapi.GetRoomInfo)
        self.streamer_info = retry_safe(self.liveapi.GetStreamerInfo)
        if not(self.room_info and self.streamer_info):
            raise RuntimeError(f'{self.taskname}: 获取主播信息出现错误.')

        os.makedirs(self.output_dir,exist_ok=True)

        stream_url = self.liveapi.GetStreamURL(**self.stream_option)
        if not stream_url:
            # 房间报在播但取不到流地址（典型：加密/密码房间）。
            # 这里直接抛出，避免把 None 当地址喂给 ffmpeg 导致秒退后无限快速重启。
            raise StreamUnavailableError(f'{self.taskname}: 无法获取直播流地址（可能是加密/密码房间）')
        stream_request_header = self.liveapi.GetStreamHeader()
        width, height = FFprobe.get_resolution(stream_url, stream_request_header)
        # 斗鱼和虎牙的直播地址只能用一次，所以要重新获取
        if self.plat == 'douyu' or self.plat == 'huya':
            stream_url = self.liveapi.GetStreamURL(**self.stream_option)

        this_engine = self.engine
        if this_engine == 'auto':
            # B站规则：带有bluray的hls流使用pyrequests，普通的hls流使用ffmpeg，flv流使用streamgears
            if self.plat == 'bilibili':
                if re.search(r'live_\d+_[a-zA-Z_]{0,10}\d+_[a-zA-Z]{1,10}', stream_url)\
                    and '.m3u8' in stream_url:
                    this_engine = 'ffmpeg'          # pyrequests强制原画可用率不高，改回ffmpeg
                elif '.m3u8' in stream_url:
                    this_engine = 'ffmpeg'
                else:
                    this_engine = 'streamgears'
            # 其他原生支持的平台hls流使用ffmpeg，flv流使用streamgears
            elif self.plat in ['huya', 'douyu', 'douyin', 'cc']:
                if '.m3u8' in stream_url:
                    this_engine = 'ffmpeg'
                else:
                    this_engine = 'streamgears'
            # streamlink支持的平台使用streamlink
            else:
                this_engine = 'streamlink'

        if this_engine == 'ffmpeg':
            from .ffmpeg import FFmpegDownloader
            downloader_class = FFmpegDownloader
        elif this_engine == 'streamgears':
            from .streamgears import StreamgearsDownloader
            downloader_class = StreamgearsDownloader
        elif this_engine == 'streamlink':
            from .streamlink import StreamlinkDownloader
            downloader_class = StreamlinkDownloader
        elif this_engine == 'pyrequests':
            from .pyrequests import PyRequestsDownloader
            downloader_class = PyRequestsDownloader
        else:
            raise NotImplementedError(f'No Downloader Named {this_engine}.')

        # output_format 跟着实际引擎走：streamgears 只会产 flv；ffmpeg 录 hls/fmp4 用 mp4
        # （H.264/HEVC 都能装进分片 mp4，且与 flv 一样抗崩）。用户在配置里显式写了就尊重其设置。
        if not self.output_format:
            self.output_format = 'mp4' if this_engine == 'ffmpeg' else 'flv'

        if not (width and height):
            default_resolution = self.advanced_video_args.get('default_resolution', (1920, 1080))
            self.logger.warning(f'无法获取视频大小，使用默认值 {default_resolution}.')
            width, height = default_resolution

        self.width,self.height = width, height
        self.logger.info(f'{self.taskname_disp}🎬录制流 {width}x{height}，容器 {self.output_format}，引擎 {this_engine}')
        self.dmw = None

        def danmaku_thread():
            self.dmw.start(
                self_segment=not self.video,
                segment_start_time=time.time() if not self.video else None,
            )

        def video_thread():
            self.downloader.start()

        self.executor = ThreadPoolExecutor(max_workers=int(self.danmaku) + int(self.video))
        futures = []

        if self.danmaku:
            description = f'{self.taskname}的录播弹幕文件, {self.url}, Powered by DanmakuRender: https://github.com/SmallPeaches/DanmakuRender.'
            danmu_output = join(self.output_dir, f'[正在录制]{self.taskname}-{time.strftime("%Y%m%d-%H%M%S",time.localtime())}-Part%03d.jsonl')
            self.dmw = DanmakuDownloader(self.url,
                                     danmu_output,
                                     self.segment,
                                     description=description,
                                     width=self.width,
                                     height=self.height,
                                     danmaku_args=self.danmaku_args)   # 弹幕全部参数(格式/连接/高级/四类型)都在这里
        if self.video:
            self.downloader = downloader_class(
                stream_url=stream_url,
                header=stream_request_header,
                output_dir=self.output_dir,
                output_format=self.output_format,
                segment=self.segment,
                url=self.url,
                taskname=self.taskname,
                advanced_video_args=self.advanced_video_args,
                segment_start_callback=self.segment_start_callback,
                segment_end_callback=self.segment_end_callback,
                debug=self.debug,
                **self.kwargs
            )

        if self.danmaku:
            futures.append(self.executor.submit(danmaku_thread))
        if self.video:
            futures.append(self.executor.submit(video_thread))

        last_onair_check = time.time()

        while not self.stoped:
            # --- 分段指令 ---
            if self._cmd_segment:
                self._cmd_segment = False
                self.logger.info(f"{self.taskname_disp}🔔收到指令：立即手动分段")
                return "segment"

            # --- 强制下播指令 ---
            if self._cmd_offline:
                self._cmd_offline = False
                self.logger.info(f"{self.taskname_disp}🔔收到指令：强制结束录制")
                self.force_stop_trigger = True
                return

            # --- 到达定时下播时间 ---
            if self.force_offline_time and is_passed_time_point(self.force_offline_time):
                self.logger.info(f"到达强制下播时间:{self.force_offline_time}")
                self.force_stop_trigger = True
                return

            # --- 等待录制结束，每1秒检查一次命令，每60秒检查一次是否下播 ---
            try:
                for future in as_completed(futures, timeout=1):
                    return future.result()
            except TimeoutError:
                if time.time() - last_onair_check >= 60:
                    last_onair_check = time.time()
                    if not self.liveapi.Onair():
                        self.logger.debug('LIVE END.')
                        return


    def get_effective_onair(self):
        if getattr(self, "force_stop_trigger", False):
            # 如果 force_stop_trigger 为 True 默认主播下播
            # 但是！ 这个参数在真的下播的时候会被设置为false
            # 需要配合record_windows 来用
            return False
        else:
            return self.liveapi.Onair()


    def start_helper(self):
        def write_time_to_txt(mode: str):
            out_dir = Path(self.output_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            file_path = out_dir / "_live_sessions.txt"

            now_str = datetime.now().isoformat(timespec="seconds")

            if mode == "start":
                # 开启新的一行记录
                with open(file_path, "a", encoding="utf-8", newline="\n") as f:
                    f.write(f"\n开播:{now_str}")
            elif mode == "end":
                # 在当前行追加结束时间
                with open(file_path, "a", encoding="utf-8", newline="\n") as f:
                    f.write(f";下播:{now_str}")
            else:
                raise ValueError(f"未知的 mode: {mode}")

        def in_record_window(now=None) -> bool:
            ranges = self.advanced_video_args.get("record_windows", {}).get("ranges")
            if not ranges:
                return True
            return is_now_in_time_ranges(ranges, now=now)

        def sleep_or_force_live(duration) -> bool:
            """睡眠 duration 秒，期间每秒检查一次手动开播指令，收到则提前返回 True"""
            waited = 0
            while waited < duration:
                if self._cmd_force_live:
                    return True
                time.sleep(min(1, duration - waited))
                waited += 1
            return self._cmd_force_live

        def sleep_or_cmd_offline(duration) -> bool:
            """睡眠 duration 秒，期间每秒检查一次「强制下播」指令，收到则清标志并提前返回 True"""
            waited = 0
            while waited < duration:
                if self._cmd_offline:
                    self._cmd_offline = False
                    return True
                time.sleep(min(1, duration - waited))
                waited += 1
            return False

        def update_state(state):
            now = datetime.now()
            now_onair = self.get_effective_onair()

            # 初始化
            if state is None:
                if now_onair:
                    state = StreamState.LIVE_START if trust_onair_at_startup else StreamState.REPLAY_START
                else:
                    state = StreamState.OFFLINE
                return state

            if state == StreamState.LIVE_END:
                return StreamState.STOPPING
            if state == StreamState.REPLAY_END:
                return StreamState.OFFLINE

            if now_onair:
                if state in (StreamState.OFFLINE, StreamState.STOPPING):
                    if self.forced_live_pending:
                        self.forced_live_pending = False
                        return StreamState.LIVE_START      # 手动开播：无视录制时段，强制开录
                    if self.forced_replay_pending:
                        self.forced_replay_pending = False
                        return StreamState.REPLAY_START
                    return (StreamState.LIVE_START if in_record_window(now) else StreamState.REPLAY_START)

                if state == StreamState.LIVE_START:
                    return StreamState.LIVE
                if state == StreamState.REPLAY_START:
                    return StreamState.REPLAY

                # LIVE/REPLAY 稳态保持
                return state

            # now_onair == False
            if state == StreamState.LIVE:
                return StreamState.LIVE_END
            if state == StreamState.REPLAY:
                return StreamState.REPLAY_END

            # OFFLINE/STOPPING 稳态保持（STOPPING -> OFFLINE 的转换由主循环在确认"真的下播"后手动设置）
            if state == StreamState.STOPPING:
                return StreamState.STOPPING

            return StreamState.OFFLINE


        # =================初始化=========================================================================
        self.loop = True
        trust_onair_at_startup = self.advanced_video_args.get("trust_onair_at_startup",True)

        stop_wait_time = int(self.stop_wait_time)
        stop_check_interval = self.advanced_video_args.get('stop_check_interval', 60)
        start_check_interval = self.advanced_video_args.get('start_check_interval', 60)
        check_policy = self.advanced_video_args.get('check_policy', {})
        # ==============interactive====================
        self.force_offline_time = self.advanced_video_args.get("force_offline_time", "")
        self._cmd_segment = False           # 手动分段
        self._cmd_offline = False           # 强制下播
        self._cmd_force_live = False        # 回放状态下手动强制开始录制
        self.force_stop_trigger = False     # 人为强制下播触发标志
        self.forced_replay_pending = False  # 人为强制下播但主播实际未下播时，下一次开播应视为回放
        self.forced_live_pending = False    # 回放状态下手动开播：下一次状态流转应无视录制时段，强制开录
        self.live_start_time = None         # 本场直播开播时间（LIVE_START 时赋值）。本文件内不读，
                                            # 由 webapi.get_tasks_data 经 getattr 取走算"本场时长"，勿误删
        # =============================================

        restart_cnt = 0
        # 连续"取不到流地址"的次数。用来区分"推流瞬断/卡顿"(短暂取不到，快速重试可恢复)
        # 和"加密/密码房或长时间无流"(持续取不到，再快也是空转)。
        stream_unavail_cnt = 0
        stream_unavail_fast_retries = 8     # 前 N 次按快速重试，超过则判定为持续无流，转慢速轮询
        # 一轮录制若录满这么久才断流，视为"正常断流"(B站CDN地址过期/节点切换导致)，
        # 平静换地址续录；不到这个时长就断，视为"异常中断"，按递增间隔重连并提示。
        normal_reconnect_min_sec = 60
        restart_interval = self.advanced_video_args.get('restart_interval', (0, 10, 60))
        if isinstance(restart_interval, (int, float)):
            restart_interval_min = restart_interval_step = restart_interval_max = restart_interval
        else:
            restart_interval_min, restart_interval_step, restart_interval_max = restart_interval

        self.sess_id = uuid(8)
        self.segment_id = 1

        # ======第一次开启程序=================================
        state = update_state(None)
        if state == StreamState.OFFLINE:
            self.logger.info(f"{self.taskname_disp}💤未开播")

        # 进入loop前的初始化
        state = None
        while self.loop:
            prev_state = state
            state = update_state(state)
            self.live_state = state
            # ---------- REPLAY end----------
            if state == StreamState.REPLAY_END:
                self.logger.info(f"{self.taskname_disp}📺回放结束")
                continue

            # ---------- REPLAY start----------(1)
            if state == StreamState.REPLAY_START:
                self.logger.info(f"{self.taskname_disp}📺回放开始")
                continue
            # ---------- REPLAY ing ----------
            if state == StreamState.REPLAY:
                self.next_check_deadline = time.monotonic() + stop_check_interval
                force_live = sleep_or_force_live(stop_check_interval)
                self.next_check_deadline = None
                if force_live:
                    self._cmd_force_live = False
                    self.forced_replay_pending = False
                    self.forced_live_pending = True   # 让下一轮 OFFLINE->LIVE_START 无视录制时段
                    self.logger.info(f"{self.taskname_disp}🔔收到指令：手动开始录制")
                    state = StreamState.OFFLINE  # 借助OFFLINE->LIVE_START的正常路径走一次livestart
                continue
            # --------- LIVE_END ----------
            if state == StreamState.LIVE_END:
                self.logger.info(f"{self.taskname_disp}⌛下播,本轮录制结束")
                write_time_to_txt("end")
                self.stop_confirm_deadline = time.monotonic() + max(0, stop_wait_time)
                continue

            # ---------- STOPPING：刚下播,等待确认是否真的下播 ----------
            if state == StreamState.STOPPING:
                restart_cnt = 0
                if self.stop_confirm_deadline is None:
                    self.stop_confirm_deadline = time.monotonic() + max(0, stop_wait_time)

                remaining = max(0.0, self.stop_confirm_deadline - time.monotonic())
                # 正常倒计时归零时，上一轮 STOPPING→STOPPING 的 update_state 已完成最终平台检查。
                # stop_wait_time=0 时本轮是 LIVE_END→STOPPING，需在收尾前补一次真正的最终检查。
                if remaining <= 0:
                    if self.force_stop_trigger:
                        # 不能用 get_effective_onair：force_stop_trigger=True 时它必定返回 False。
                        # 人为下播在完成确认等待后查真实平台状态；仍在线则下一轮进入 REPLAY。
                        if self.liveapi.Onair():
                            self.forced_replay_pending = True
                    elif prev_state != StreamState.STOPPING and self.get_effective_onair():
                        self.stop_confirm_deadline = None
                        state = StreamState.LIVE
                        continue
                    self.logger.info(f"{self.taskname_disp}🔴直播真的结束了")
                    self._pipeSend('liveend', '直播真的结束了', data=self.sess_id)

                    self.sess_id = uuid(8)
                    self.segment_id = 1
                    self._reset_segment_tracking()
                    self.force_stop_trigger = False
                    self.stop_confirm_deadline = None
                    state = StreamState.OFFLINE
                    continue

                interval = get_check_interval(stop_check_interval,check_policy)
                wait_time = min(interval, remaining)
                # 逐秒小步睡：期间收到「直接下播」指令就跳过剩余确认等待，立即按真下播收尾
                skip_wait = sleep_or_cmd_offline(wait_time)
                if skip_wait:
                    self.logger.info(f"{self.taskname_disp}🔔收到指令：跳过下播确认等待，立即下播")
                    self.force_stop_trigger = True   # 与录制中「强制下播」同语义：主播实际在线则下次按回放
                if skip_wait:
                    self.logger.info(f"{self.taskname_disp}🔴直播真的结束了")
                    self._pipeSend('liveend', '直播真的结束了', data=self.sess_id)

                    self.sess_id = uuid(8)
                    self.segment_id = 1
                    self._reset_segment_tracking()

                    # 如果是人为强制下播（force_offline_time/cmd_offline）但主播实际未下播，
                    # 下一次检测到在线时应视为回放，而不是继续按直播处理
                    if self.force_stop_trigger and self.liveapi.Onair():
                        self.forced_replay_pending = True

                    self.force_stop_trigger = False
                    self.stop_confirm_deadline = None
                    state = StreamState.OFFLINE
                continue

            # ---------- OFFLINE ----------(2)
            if state == StreamState.OFFLINE:
                restart_cnt = 0
                stream_unavail_cnt = 0
                interval = get_check_interval(start_check_interval,check_policy)
                self.stop_confirm_deadline = None
                self.next_check_deadline = time.monotonic() + interval
                # 超时=正常自动检测；Event 被置位=WebUI 要求立即检测或任务正在停止。
                self._check_wakeup.wait(interval)
                self._check_wakeup.clear()
                self.next_check_deadline = None
                continue

            # ---------- LIVE_START ----------
            if state == StreamState.LIVE_START:
                self.stop_confirm_deadline = None
                self.next_check_deadline = None
                # 清掉上一场残留的下播指令：指令若恰好在 STOPPING→OFFLINE 切换瞬间发出、
                # 没被任何循环消费，残留标志会把新一场直播秒杀掉
                self._cmd_offline = False
                if prev_state == StreamState.STOPPING:
                    self.logger.info(f"{self.taskname_disp}🔄再次开播,重启录制")
                else:
                    self.live_start_time = datetime.now()   # 记录本场开播时间
                    self._pipeSend('livestart', '直播开始', dtype='str', data=self.sess_id,url=self.url)
                    self.logger.info(f"{self.taskname_disp}🔔直播开始")
                    write_time_to_txt("start")
                state = StreamState.LIVE # 直接切换为稳态 防止不知名bug
                continue

            # ---------- LIVE：允许录制 ----------(3)
            try:
                res = self.start_once()
                stream_unavail_cnt = 0   # 成功取到流并录过一轮，连续失败计数清零

                if res == "segment":
                    self.stop_once()
                    self.logger.info(f"{self.taskname_disp}🔄手动分段结束,重启录制")
                    continue

                # start_once 正常返回，但主播还在播：ffmpeg 自己退出了（多为 B站 CDN 地址
                # 过期/节点切换导致的断流）。按"这一轮录了多久"区分正常断流和异常中断。
                if update_state(state) == StreamState.LIVE:
                    self.stop_once()
                    recorded = (
                        (datetime.now() - self.segment_start_time).total_seconds()
                        if self.segment_start_time else 0
                    )
                    if recorded >= normal_reconnect_min_sec:
                        # 录够久才断 → 正常断流，平静换地址续录，不当异常、不累加重启计数
                        restart_cnt = 0
                        self.logger.info(f"{self.taskname_disp}🔗直播流断开(本段已录{int(recorded//60)}分{int(recorded%60)}秒)，正在重连续录...")
                        time.sleep(restart_interval_min)
                        continue
                    else:
                        # 很快就断 → 地址失效/网络异常，按递增间隔重连并提示
                        self.logger.warning(f"{self.taskname_disp}⚠️录制{int(recorded)}秒后异常中断，第{restart_cnt + 1}次重连...")
                        time.sleep(min(restart_interval_min + restart_interval_step * restart_cnt, restart_interval_max))
                        restart_cnt += 1
                        continue

            except KeyboardInterrupt:
                self.stop()
                exit(0)

            except StreamUnavailableError as e:
                # 取不到流地址。房间会一直报在播，若一律快速重启会无限空转。
                self.stop_once()
                stream_unavail_cnt += 1
                if stream_unavail_cnt <= stream_unavail_fast_retries:
                    # 前几次先当成推流瞬断/卡顿：按递增间隔快速重试，等推流恢复就能自动接上。
                    interval = min(restart_interval_min + restart_interval_step * (stream_unavail_cnt - 1), restart_interval_max)
                    self.logger.info(f"{self.taskname_disp}🔄取流失败，{interval}秒后重试(第{stream_unavail_cnt}次)...")
                else:
                    # 连续多次仍取不到流 → 判定为加密/密码房或长时间无流，转慢速轮询，避免无限空转。
                    interval = get_check_interval(start_check_interval, check_policy)
                    if stream_unavail_cnt == stream_unavail_fast_retries + 1:
                        self.logger.warning(f"{self.taskname_disp}🔒持续取不到流(可能是加密/密码房间)，转为每{interval}秒检测一次...")
                time.sleep(interval)
                continue

            except Exception as e:
                if update_state(state) == StreamState.LIVE:
                    # start_once 内部直接抛异常（较少见，如取流/建进程出错），属异常中断
                    self.stop_once()
                    self.logger.warning(f"{self.taskname_disp}⚠️录制异常中断，第{restart_cnt + 1}次重连...")
                    self.logger.exception(e)
                    time.sleep(min(restart_interval_min + restart_interval_step * restart_cnt, restart_interval_max))
                    restart_cnt += 1
                    continue
                else:
                    self.logger.debug(f"Downloader异常退出:{e}")

            self.logger.debug(f'{self.taskname} stop once.')
            self.stop_once()



    def start(self):
        thread = threading.Thread(target=self.start_helper,daemon=True)
        thread.start()
        return thread

    def stop(self):
        self.loop = False
        self._check_wakeup.set()
        self.stop_once()
        self._pipeSend('livestop', '录制终止', dtype='str', data=self.sess_id if hasattr(self, 'sess_id') else None)

    # ------------------------------------------------------------------
    # API 实时命令方法（由 Downloader 插件转发，WebAPI 调用）
    # ------------------------------------------------------------------

    def cmd_segment(self):
        """立即手动分段"""
        self._cmd_segment = True

    def cmd_offline(self):
        """强制结束当前录制"""
        self._cmd_offline = True

    def cmd_force_offline_time(self, time_str: str):
        """设置/取消定时下播，time_str 格式 HH:MM，传空字符串取消（任意状态下立即生效）"""
        if time_str == "":
            self.force_offline_time = ""
            self.logger.info(f"{self.taskname_disp}🚫收到指令：已取消强制下播时间限制")
        elif re.match(r"^\d{1,2}:\d{2}$", time_str):
            self.force_offline_time = time_str
            self.logger.info(f"{self.taskname_disp}🔔收到指令：修改强制下播时间为 {time_str}")
        else:
            self.logger.error(f"时间格式错误: '{time_str}'，请使用 HH:MM 格式或留空取消")

    def cmd_force_live(self):
        """回放状态下手动强制开始录制"""
        self._cmd_force_live = True

    def cmd_check_live(self):
        """未开播时立即唤醒一次自动检测；同一任务按单调时钟做3秒后端冷却。"""
        if getattr(self, 'live_state', None) != StreamState.OFFLINE:
            return False, "任务当前不是未开播状态"
        now = time.monotonic()
        with self._check_now_lock:
            remaining = 3.0 - (now - self._last_check_now)
            if remaining > 0:
                return False, f"操作过于频繁，请 {remaining:.1f} 秒后再试"
            self._last_check_now = now
        self._check_wakeup.set()
        return True, "立即检测指令已发送"

    def stop_once(self): # 视频后端会通过 segment_end_callback 收尾当前文件
        self.stoped = True
        if self.video and hasattr(self, 'downloader'):
            try:
                self.downloader.stop()
            except Exception as e:
                self.logger.exception(e)
        if self.danmaku and hasattr(self, 'dmw') and self.dmw:
            try:
                # 视频后端先通过 segment_end_callback 收尾已有视频；此时仍留在
                # dm_file 中的临时 JSONL 没有对应视频，应在关闭 writer 后删除。
                # 纯弹幕任务没有视频，必须保留其当前 JSONL。
                self.dmw.stop(discard_unpaired=self.video)
                with self._segment_lock:
                    self._dm_preopened_start_time = None
            except Exception as e:
                self.logger.exception(e)
        try:
            if hasattr(self, 'executor'):
                self.executor.shutdown(wait=False)
        except Exception as e:
            self.logger.exception(e)
        with self._segment_lock:
            if self._segment_contexts:
                self.logger.warning(
                    "停止本轮录制时仍有 %d 个未收到结束事件的视频段",
                    len(self._segment_contexts),
                )
                self._segment_contexts.clear()
