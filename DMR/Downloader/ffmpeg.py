from datetime import datetime
import subprocess
import sys
import threading
import time
import queue
import logging
from os.path import *

from DMR.utils import *

class FFmpegDownloader():
    default_header = {
            'Content-Type': 'application/x-www-form-urlencoded',
            'User-Agent': 'Mozilla/5.0 (Linux; Android 5.0; SM-G900P Build/LRX21T) AppleWebKit/537.36 '
                            '(KHTML, like Gecko) Chrome/75.0.3770.100 Mobile Safari/537.36 '
        }
    
    def __init__(self, 
                 stream_url:str, 
                 output_dir:str,
                 output_format:str,
                 taskname:str='',
                 segment:int=None,
                 header:dict=None,
                 ffmpeg:str=None,
                 segment_start_callback=None,
                 segment_end_callback=None,
                 advanced_video_args:dict=None,
                 debug=False,
                 **kwargs):
        
        self.stream_url = stream_url
        self.output_dir = output_dir
        self.output_format = output_format
        self.header = header if header else self.default_header
        self.segment = segment
        self.debug = debug
        self.taskname = taskname
        self.segment_start_callback = segment_start_callback
        self.segment_end_callback = segment_end_callback
        self.advanced_video_args = advanced_video_args if advanced_video_args else {}
        self.kwargs = kwargs
        self.ffmpeg = ffmpeg if ffmpeg else ToolsList.get('ffmpeg')
        self.logger = logging.getLogger(__name__)
        
        self.ffmpeg_proc = None
        self.stoped = False
    
    @property
    def duration(self):
        return datetime.now().timestamp() - self.start_time

    def _handle_opening_line(self, event_time, line):
        if 'Opening' not in line:
            return
        try:
            fname = line.split("'")[1]
        except IndexError:
            return
        if fname.startswith('http'):
            return
        if self.thisfile:
            self.segment_end_callback(
                self.thisfile,
                event_time,
                continuous=True,
            )
        self.thisfile = fname
        self.segment_start_callback(fname, event_time)
        
    def start_ffmpeg(self):
        ffmpeg_stream_args = self.advanced_video_args.get('ffmpeg_stream_args', 
                                                          [ '-rw_timeout','10000000',
                                                            '-analyzeduration','15000000',
                                                            '-probesize','50000000',
                                                            '-thread_queue_size', '16'])
        ffmpeg_args = [
            self.ffmpeg, '-y','-hide_banner',
            '-headers', ''.join('%s: %s\r\n' % x for x in self.header.items()),
            *ffmpeg_stream_args,
            '-i', self.stream_url,
            '-c', 'copy'
        ]

        # 输出封装参数。mp4 类容器要分片(frag_keyframe+empty_moov)才能「边录边看」且抗崩：
        #   - 分段录制(-f segment)时，分片参数必须用 -segment_format_options 传给内层 mp4 封装器；
        #     直接写 -movflags 会被 segment 外层封装器吃掉、不生效，导致当前分段没有 moov、录制中打不开。
        #   - flv 是流式容器，天然可边录边看，不需要这些参数。
        # 用户在 advanced_args.ffmpeg_output_args 里显式写了就完全按其配置。
        user_output_args = self.advanced_video_args.get('ffmpeg_output_args')
        is_mp4 = self.output_format in ('mp4', 'mov', 'm4s')

        if self.segment:
            ffmpeg_args += ['-f','segment',
                            '-segment_time',str(self.segment),
                            '-reset_timestamps','1']
            if user_output_args is not None:
                ffmpeg_args += user_output_args
            elif is_mp4:
                ffmpeg_args += ['-segment_format_options','movflags=+frag_keyframe+empty_moov']
            ffmpeg_args += [self.raw_name]
        else:
            if user_output_args is not None:
                ffmpeg_args += user_output_args
            elif is_mp4:
                ffmpeg_args += ['-movflags','+frag_keyframe+empty_moov']
            ffmpeg_args += [self.raw_name]

        
        self.logger.debug('FFmpegDownloader args:')
        self.logger.debug(ffmpeg_args)

        self.ffmpeg_proc = subprocess.Popen(
            ffmpeg_args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            universal_newlines=True,
            encoding='utf-8',
            errors='ignore',
        )
        
        self.msg_queue = None
        def ffmpeg_monitor():
            while not self.stoped:
                if self.ffmpeg_proc.stdout.readable():
                    line = self.ffmpeg_proc.stdout.readline().strip()
                    if len(line) > 0:
                        # 在读取到 FFmpeg 日志的线程立即取时钟，避免主循环最多约1秒的
                        # 消费延迟污染真实文件边界。
                        self.msg_queue.put((time.time(), line))
        
        if self.ffmpeg_proc.stdout is not None:
            self.msg_queue = queue.Queue()
            self.ffmpeg_monitor_proc = threading.Thread(target=ffmpeg_monitor, daemon=True)
            self.ffmpeg_monitor_proc.start()

        return self.msg_queue
    
    def start_helper(self):
        self.stoped = False
        self.raw_name = join(self.output_dir, f'[正在录制]{self.taskname}-{time.strftime("%Y%m%d-%H%M%S",time.localtime())}-Part%03d.{self.output_format}')
        self.start_time = datetime.now().timestamp()
        self._timer_cnt = 1
        self.thisfile = None

        log = ''
        ffmpeg_low_speed = 0

        self.start_ffmpeg()

        def is_info_line(line:str):
            return bool(line.startswith('frame=') or line.startswith('size=') or 'time=' in line)
        
        self.download_stable = False # stable ffmpeg speed < 2
        while not self.stoped:
            if self.ffmpeg_proc.poll() is not None:
                while self.msg_queue and not self.msg_queue.empty():
                    queued_time, queued_line = self.msg_queue.get_nowait()
                    self._handle_opening_line(queued_time, queued_line)
                if self.thisfile:
                    self.segment_end_callback(self.thisfile, time.time())
                    self.thisfile = None
                self.logger.debug('FFmpeg exit.')
                self.logger.debug(log)
                raise RuntimeError(f'FFmpeg 退出.')
            
            event_time = None
            line = ''
            try:
                event_time, line = self.msg_queue.get_nowait()
                if self.debug:
                    self.logger.debug("%s ffmpeg: %s", self.taskname, line)
                if not line.startswith('[hls'):
                    log += line + '\n'
            except queue.Empty:
                time.sleep(1)
            
            if is_info_line(line):
                if not self.advanced_video_args.get('disable_lowspeed_interrupt'):
                    l = line.find('speed=')
                    r = line.find('x', l)
                    if l > 0 and r > 0:
                        speed = float(line[l:r][6:])
                        if speed < 0.8:
                            ffmpeg_low_speed += 1
                            if ffmpeg_low_speed % 5 == 3:
                                self.logger.warn(f'{self.taskname} 直播流下载速度过慢, 请保证网络带宽充足.')
                            if ffmpeg_low_speed >= 15:
                                self.logger.debug(log)
                                raise RuntimeError(f'{self.taskname} 下载速度过慢, 即将重试.')
                        else:
                            ffmpeg_low_speed = 0

                        if not self.download_stable and speed and speed < 2:
                            self.download_stable = True
            
            self._handle_opening_line(event_time, line)

            if 'dropping it' in line or 'Invalid NAL unit size' in line:
                self.logger.debug(log)
                raise RuntimeError(f'{self.taskname} 直播流读取错误, 即将重试, 如果此问题多次出现请反馈.')

            if self.start_time is not None and self.duration > self._timer_cnt*15:
                if len(log) == 0:
                    raise RuntimeError(f'{self.taskname} 管道读取错误, 即将重试.')

                ok = False
                for li in log.split('\n'):
                    if li and is_info_line(li):
                        ok = True
                        break

                if not ok:
                    raise RuntimeError(f'{self.taskname} 直播流读取错误, 即将重试: {log}')

                log = ''
                self._timer_cnt += 1

    def start(self):
        return self.start_helper()

    def stop(self):
        if self.stoped:
            return
        self.stoped = True
        log = ''
        try:
            while self.msg_queue.qsize() > 0:
                event_time, msg = self.msg_queue.get_nowait()
                self._handle_opening_line(event_time, msg)
                log += msg+'\n'
        except Exception as e:
            self.logger.debug(e)
        if log:
            self.logger.debug(f'{self.taskname} ffmpeg: {log}')
            
        try:
            out, _ = self.ffmpeg_proc.communicate('q',timeout=3)
            if out:
                self.logger.debug(f'ffmpeg out: {out}.')
        except Exception as e:
            self.ffmpeg_proc.kill()
            self.logger.debug(e)
        
        if self.thisfile:
            end_time = time.time()
            # 时间在 FFmpeg 退出后立即固定；等待仅用于确保文件句柄和尾部数据稳定，
            # 不能把这1秒算进视频的现实时间边界。
            time.sleep(1)
            self.segment_end_callback(self.thisfile, end_time)
            self.thisfile = None
        self.logger.debug('ffmpeg downloader stoped.')
