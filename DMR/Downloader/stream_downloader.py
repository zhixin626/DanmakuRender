import logging
import os
import queue
import threading
import time

from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError
from os.path import join,exists,splitext
from datetime import datetime
from DMR.Downloader.Danmaku import DanmakuDownloader
from DMR.LiveAPI import *
from DMR.utils import *
from pathlib import Path
from DMR.utils.merge_mp4 import COLORS, format_duration,pad_disp
from DMR.utils.seconds_until import is_now_in_time_ranges,get_start_check_interval
from enum import Enum
class StreamState(str, Enum):
    OFFLINE = "offline"                  # 稳态：不在播
    LIVE = "live"                        # 稳态：直播中（允许录）
    REPLAY = "replay"                    # 稳态：回放/录像中（禁录）
    LIVE_END = "live_end"        # 瞬时态：直播刚结束（要发 liveend）
    REPLAY_END = "replay_end"    # 瞬时态：回放刚结束（不发 liveend，仅复位）
    LIVE_START = "live_start"
    REPLAY_START = "replay_start"

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

        # if self.engine not in ['ffmpeg', 'streamlink', 'streamgears', 'pyrequests', 'auto']:
        #     raise NotImplementedError(f'No Downloader Named {self.engine}.')

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
            stime=self.live_start_time if hasattr(self,"live_start_time") else datetime.now(),    #<<<<<添加！！---------------------------
            etime=self.live_end_time if hasattr(self,"live_end_time") else datetime.now(),    #<<<<<添加！！---------------------------
            totaltime=format_duration(self.live_start_time,self.live_end_time) if hasattr(self,"live_end_time") and hasattr(self,"live_start_time") else "0",
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

    def start_once(self, mode: str = "normal_mode"):
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
                    this_engine = 'pyrequests'
                elif '.m3u8' in stream_url:
                    this_engine = 'ffmpeg'
                else:
                    this_engine = 'streamgears'
            # # 虎牙必须使用ffmpeg (https://github.com/SmallPeaches/DanmakuRender/issues/386)
            # elif self.plat == 'huya':
            #     this_engine = 'ffmpeg'
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
        
        if mode == "normal_mode":

            while not self.stoped:

                try:
                    for future in as_completed(futures, timeout=60):
                        return future.result()
                except TimeoutError:
                    if self.liveapi.Onair() == False:
                        self.logger.debug('LIVE END.')
                        return

        elif mode == "test_mode":
            self.logger.info(f"正在进行test_mode录制,时长{self.test_max_seconds}秒...")

            while not self.stoped:
                elapsed = (datetime.now() - self.segment_start_time).total_seconds()
                if elapsed >= self.test_max_seconds:
                    self.logger.info(f"{self.taskname_disp}: 达到 {self.test_max_seconds}s 测试上限，后续禁录")
                    return
                remaining = self.test_max_seconds - elapsed
                timeout = max(1, min(60, remaining))

                try:
                    for future in as_completed(futures, timeout=timeout):
                        return future.result()
                except TimeoutError:
                    if self.liveapi.Onair() == False:
                        self.logger.debug('LIVE END.')
                        return

        elif mode == "firstsegment_mode":
            self.logger.info(f"{self.taskname_disp}:正在进行firstsegment_mode录制,时长{self.firstsegment_seconds}秒...")

            while not self.stoped:
                elapsed = (datetime.now() - self.segment_start_time).total_seconds()
                if elapsed >= self.firstsegment_seconds:
                    self.logger.info(f"{self.taskname_disp}:达到{self.firstsegment_seconds}秒，执行一次受控切段")
                    self.stop_once()                   # ⭐关键：触发 downloader.stop() -> segment_callback 链路
                    return
                remaining = self.firstsegment_seconds - elapsed
                timeout = max(1, min(60, remaining))

                try:
                    for future in as_completed(futures, timeout=timeout):
                        return future.result()
                except TimeoutError:
                    if self.liveapi.Onair() == False:
                        self.logger.debug('LIVE END.')
                        return

    def get_effective_onair(self):
        if getattr(self, "test_mode_end", False):
            return False
        else:
            return self.liveapi.Onair()


    def start_helper(self):
        def write_time_to_txt(mode: str):
            out_dir = Path(self.output_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            now = datetime.now()
            if mode == "start":
                file = "_livestart_times.txt"
            elif mode == "end":
                file = "_liveend_times.txt"
            else:
                raise ValueError(f"未知的 mode: {mode}")
            with open(out_dir / file, "a", encoding="utf-8", newline="\n") as f:
                f.write(now.isoformat(timespec="seconds") + "\n")

        def in_record_window(now=None) -> bool:
            if not enable_record_windows:
                return True
            ranges = self.advanced_video_args.get("record_windows", {}).get("ranges", [])
            return is_now_in_time_ranges(ranges, now=now)

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

            if state in (StreamState.LIVE_END, StreamState.REPLAY_END):
                return StreamState.OFFLINE

            if now_onair:
                if state == StreamState.OFFLINE:
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

            return StreamState.OFFLINE


        # =================初始化=========================================================================
        self.loop = True
        enable_record_windows = self.advanced_video_args.get("record_windows", {}).get("enabled", False)
        trust_onair_at_startup = self.advanced_video_args.get("trust_onair_at_startup",True)

        stop_waited = 0
        stop_wait_time = int(self.stop_wait_time * 60)
        stop_check_interval = self.advanced_video_args.get('stop_check_interval', 30)
        # ===============testmode====================
        tm = self.advanced_video_args.get("test_mode", {}) or {}
        self.test_enabled = bool(tm.get("enabled", False))
        self.test_max_seconds = int(tm.get("max_record_seconds", 180))
        self.test_mode_end = False
        # ===============first_segment_mode====================
        fs = self.advanced_video_args.get("firstsegment", {}) or {}
        self.firstsegment_enabled = bool(fs.get("enabled", False))
        self.firstsegment_seconds = int(fs.get("seconds", 180))
        self.firstsegment_mode_end = False

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
        live_truely_end = True
        state = None
        while self.loop:
            state = update_state(state)
            # ---------- REPLAY end----------
            if state == StreamState.REPLAY_END:
                self.logger.info(f"{self.taskname_disp}📺回放结束")
                continue

            # ---------- REPLAY start----------(1)
            if state == StreamState.REPLAY_START:
                self.logger.info(f"{self.taskname_disp}📺回放开始")
                time.sleep(stop_check_interval)
                continue
            # ---------- REPLAY ing ----------
            if state == StreamState.REPLAY:
                time.sleep(stop_check_interval)
                continue
            # --------- LIVE_END / REPLAY_END ----------
            if state == StreamState.LIVE_END:
                self.logger.info(f"{self.taskname_disp}⌛下播,本轮录制结束")
                now = datetime.now()
                self.live_end_time = now
                write_time_to_txt("end")
                stop_waited = 0
                live_truely_end = False
                continue

            # ---------- OFFLINE ----------(2)
            if state == StreamState.OFFLINE:
                restart_cnt = 0
                if live_truely_end:
                    interval = get_start_check_interval(self.advanced_video_args)
                    time.sleep(interval)
                else:
                    time.sleep(stop_check_interval)
                    stop_waited += stop_check_interval

                if stop_waited > stop_wait_time and not live_truely_end:
                    live_truely_end = True
                    self._pipeSend('liveend', '直播真的结束了', data=self.sess_id)
                    self.logger.info(f"{self.taskname_disp}🔴直播真的结束了")
                    self.sess_id = uuid(8)
                    self.segment_id = 1
                continue

            # ---------- LIVE_START ----------
            if state == StreamState.LIVE_START:
                if live_truely_end:
                    self._pipeSend('livestart', '直播开始', dtype='str', data=self.sess_id)
                    self.logger.info(f"{self.taskname_disp}🔔直播开始")
                    now = datetime.now()
                    self.live_start_time = now
                    write_time_to_txt("start")
                else:
                    self.logger.info(f"{self.taskname_disp}🔄再次开播,重启录制")
                state = StreamState.LIVE # 直接切换为稳态 防止不知名bug
                continue

            # ---------- LIVE：允许录制 ----------(3)
            try:
                stop_waited = 0
                live_truely_end = False

                if self.test_enabled:
                    self.start_once(mode="test_mode")
                    self.test_mode_end = True # 给get_effective_onair()用,让后续开播都不视为未开播|模拟正常下播
                elif self.firstsegment_enabled and not self.firstsegment_mode_end:
                    self.start_once(mode="firstsegment_mode")
                    self.firstsegment_mode_end=True
                    continue
                else:
                    self.start_once(mode="normal_mode")

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
