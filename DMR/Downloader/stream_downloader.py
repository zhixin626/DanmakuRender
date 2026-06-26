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

class StreamDownloadTask(): # 被上层class Downloader():的new_task函数中初始化
    def __init__(self,
                 url,
                 output_dir,
                 send_queue:queue.Queue,
                 segment:int,
                 output_name=None,
                 taskname=None,
                 danmaku=True,
                 video=True,
                 stop_wait_time=0,
                 output_format='flv',
                 stream_option=None,
                 advanced_video_args:dict=None,
                 advanced_dm_args:dict=None,
                 engine='ffmpeg',
                 debug=False,
                 gift_dm_args={},
                 sc_dm_args={},
                 **kwargs
        ) -> None:
        self.taskname = taskname
        self.url = url
        self.plat, self.rid = split_url(url)
        self.liveapi = LiveAPI(url)
        self.output_dir = output_dir or './' + self.taskname
        self.output_format = output_format
        self.output_name = output_name
        self.send_queue = send_queue
        self.logger = logging.getLogger(__name__)
        self.kwargs = kwargs
        self.debug = debug
        self.segment = segment
        self.danmaku = danmaku
        self.video = video
        self.stream_option = stream_option
        self.stop_wait_time = stop_wait_time
        self.engine = engine or 'auto'
        self.advanced_video_args = advanced_video_args if advanced_video_args else {}
        self.advanced_dm_args = advanced_dm_args if advanced_dm_args else {}
        self.gift_dm_args=gift_dm_args
        self.sc_dm_args=sc_dm_args
        self.stoped = True
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

    def stable_callback(self, time_error):
        if hasattr(self, 'dmw') and self.dmw:
            self.dmw.time_fix(time_error)

    def segment_callback(self, filename:str):
        if self.room_info is None or not exists(filename):
            self.logger.debug(f'No video file {filename}')
            return

        duration = datetime.now().timestamp()-self.segment_start_time.timestamp()
        # 如果视频时长小于60秒，尝试使用ffprobe获取视频时长
        if duration < 60:
            duration = max(duration, FFprobe.get_duration(filename))

        video_info = VideoInfo(
            path=filename,
            file_id=uuid(),
            dtype='src_video',
            group_id=self.sess_id,
            segment_id=self.segment_id,
            size=os.path.getsize(filename),
            ctime=self.segment_start_time,
            duration=duration,
            resolution=(self.width, self.height),
            title=self.room_info['title'],
            streamer=self.streamer_info,
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

        if self.danmaku:
            newdmfile = splitext(newfile)[0]+'.'+self.kwargs.get('dm_format', 'ass')
            self.dmw.split(newdmfile)
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
            self.segment_start_time = datetime.now()
            return

        self._pipeSend(event='livesegment', msg=f'视频分段 {newfile} 录制完成.', target=f'replay/{self.taskname}', dtype='VideoInfo', data=video_info)
        new_room_info = retry_safe(self.liveapi.GetRoomInfo)
        if new_room_info:
            self.room_info = new_room_info
        self.segment_start_time = datetime.now()
        self.segment_id += 1

    def start_once(self):
        self.stoped = False

        # init segment info
        self.room_info = retry_safe(self.liveapi.GetRoomInfo)
        self.streamer_info = retry_safe(self.liveapi.GetStreamerInfo)
        if not(self.room_info and self.streamer_info):
            raise RuntimeError(f'{self.taskname}: 获取主播信息出现错误.')

        self.segment_start_time = datetime.now()
        os.makedirs(self.output_dir,exist_ok=True)

        stream_url = self.liveapi.GetStreamURL(**self.stream_option)
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

        if not (width and height):
            default_resolution = self.advanced_video_args.get('default_resolution', (1920, 1080))
            self.logger.warning(f'无法获取视频大小，使用默认值 {default_resolution}.')
            width, height = default_resolution

        self.width,self.height = width, height

        self.downloader = None
        self.dmw = None

        def danmaku_thread():
            description = f'{self.taskname}的录播弹幕文件, {self.url}, Powered by DanmakuRender: https://github.com/SmallPeaches/DanmakuRender.'
            danmu_output = join(self.output_dir, f'[正在录制]{self.taskname}-{time.strftime("%Y%m%d-%H%M%S",time.localtime())}-Part%03d.ass')
            self.dmw = DanmakuDownloader(self.url,
                                     danmu_output,
                                     self.segment,
                                     description=description,
                                     width=self.width,
                                     height=self.height,
                                     advanced_dm_args=self.advanced_dm_args,
                                     gift_dm_args=self.gift_dm_args,   # 礼物文件路径/是否录/门槛都由 DanmakuDownloader 自己从这里(及 output 目录)取
                                     sc_dm_args=self.sc_dm_args,             # SC 布局参数（如 anchor_y_ratio）
                                     **self.kwargs)

            self.dmw.start(self_segment=not self.video)

        def video_thread():
            self.downloader = downloader_class(
                stream_url=stream_url,
                header=stream_request_header,
                output_dir=self.output_dir,
                output_format=self.output_format,
                segment=self.segment,
                url=self.url,
                taskname=self.taskname,
                advanced_video_args=self.advanced_video_args,
                segment_callback=self.segment_callback,
                stable_callback=self.stable_callback,
                debug=self.debug,
                **self.kwargs
            )
            self.downloader.start()

        self.executor = ThreadPoolExecutor(max_workers=int(self.danmaku) + int(self.video))
        futures = []


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

        stop_waited = 0
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
        self.live_start_time = None         # 本场直播开播时间（LIVE_START 时赋值）。本文件内不读，
                                            # 由 webapi.get_tasks_data 经 getattr 取走算"本场时长"，勿误删
        # =============================================

        restart_cnt = 0
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
                if sleep_or_force_live(stop_check_interval):
                    self._cmd_force_live = False
                    self.forced_replay_pending = False
                    self.logger.info(f"{self.taskname_disp}🔔收到指令：手动开始录制")
                    state = StreamState.OFFLINE  # 借助OFFLINE->LIVE_START的正常路径走一次livestart
                continue
            # --------- LIVE_END ----------
            if state == StreamState.LIVE_END:
                self.logger.info(f"{self.taskname_disp}⌛下播,本轮录制结束")
                write_time_to_txt("end")
                stop_waited = 0
                continue

            # ---------- STOPPING：刚下播,等待确认是否真的下播 ----------
            if state == StreamState.STOPPING:
                restart_cnt = 0
                interval = get_check_interval(stop_check_interval,check_policy)
                time.sleep(interval)
                stop_waited += interval
                if stop_waited > stop_wait_time:
                    self.logger.info(f"{self.taskname_disp}🔴直播真的结束了")
                    self._pipeSend('liveend', '直播真的结束了', data=self.sess_id)

                    self.sess_id = uuid(8)
                    self.segment_id = 1

                    # 如果是人为强制下播（force_offline_time/cmd_offline）但主播实际未下播，
                    # 下一次检测到在线时应视为回放，而不是继续按直播处理
                    if self.force_stop_trigger and self.liveapi.Onair():
                        self.forced_replay_pending = True

                    self.force_stop_trigger = False
                    state = StreamState.OFFLINE
                continue

            # ---------- OFFLINE ----------(2)
            if state == StreamState.OFFLINE:
                restart_cnt = 0
                interval = get_check_interval(start_check_interval,check_policy)
                time.sleep(interval)
                continue

            # ---------- LIVE_START ----------
            if state == StreamState.LIVE_START:
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
                stop_waited = 0

                res = self.start_once()

                if res == "segment":
                    self.stop_once()
                    self.logger.info(f"{self.taskname_disp}🔄手动分段结束,重启录制")
                    continue

                if update_state(state) == StreamState.LIVE:
                    raise RuntimeError(f'{self.taskname} 录制异常退出.')

            except KeyboardInterrupt:
                self.stop()
                exit(0)

            except Exception as e:
                if update_state(state) == StreamState.LIVE:
                    self.logger.info(f"{self.taskname_disp}🔄第{restart_cnt + 1}次重启,等待后重新开始录制...")
                    self.logger.exception(e)
                    self.stop_once()
                    time.sleep(min(restart_interval_min + restart_interval_step * restart_cnt, restart_interval_max))
                    self.logger.info(f"{self.taskname_disp}🔄重启录制")
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

    def stop_once(self): # 会调用segment callback分段视频
        self.stoped = True
        if self.video and hasattr(self, 'downloader'):
            try:
                self.downloader.stop()
            except Exception as e:
                self.logger.exception(e)
        if self.danmaku and hasattr(self, 'dmw') and self.dmw:
            try:
                self.dmw.stop()
            except Exception as e:
                self.logger.exception(e)
        try:
            if hasattr(self, 'executor'):
                self.executor.shutdown(wait=False)
        except Exception as e:
            self.logger.exception(e)
