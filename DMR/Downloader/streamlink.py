import logging
import random
import subprocess
import re
import glob
import os

from datetime import datetime
from os.path import splitext, split, join, exists
import tempfile
import time

from DMR.utils import *
from DMR.LiveAPI import Onair


class StreamlinkDownloader():
    def __init__(self,  
                 output_dir:str,
                 segment:int,
                 url:str,
                 taskname:str,
                 output_format:str,
                 debug=False,
                 segment_start_callback=None,
                 segment_end_callback=None,
                 **kwargs):
        
        self.output_dir = output_dir
        self.output_format = output_format
        self.segment = segment
        self.debug = debug
        self.taskname = taskname
        self.url = url
        self.segment_start_callback = segment_start_callback
        self.segment_end_callback = segment_end_callback
        self.kwargs = kwargs

        self.advanced_video_args = kwargs.get('advanced_video_args', {})
        self.logger = logging.getLogger(__name__)
        self.stoped = False
        self._active_file = None
        self._started_files = set()
        self._ended_files = set()

    def _file_start_time(self, path):
        try:
            return os.path.getctime(path)
        except OSError:
            return time.time()

    def _observe_files(self, final=False):
        files = sorted(glob.glob(join(self.output_dir, f'*{self.uuid}*')))
        for path in files:
            if path in self._started_files:
                continue
            start_time = self._file_start_time(path)
            if self._active_file and self._active_file not in self._ended_files:
                self.segment_end_callback(
                    self._active_file,
                    start_time,
                    continuous=True,
                )
                self._ended_files.add(self._active_file)
            self.segment_start_callback(path, start_time)
            self._started_files.add(path)
            self._active_file = path

        if final and self._active_file and self._active_file not in self._ended_files:
            self.segment_end_callback(self._active_file, time.time())
            self._ended_files.add(self._active_file)
    
    def start_helper(self):
        raw_name = join(self.output_dir, f'[正在录制]{self.taskname}-{time.strftime("%Y%m%d%H%M%S",time.localtime())}-Part%03d-{self.uuid}.{self.output_format}')

        port = random.randint(10000, 65535)
        streamlink_extra_args = self.advanced_video_args.get('streamlink_extra_args') or []
        streamlink_quality = self.advanced_video_args.get('streamlink_quality') or 'best'
        streamlink_args = [
            "streamlink",
            "--player-external-http",  # 为外部程序提供流媒体数据
            "--player-external-http-port", str(port),  # 对外部输出流的端口
            *streamlink_extra_args,
            self.url,
            streamlink_quality,
        ]
        self.logger.debug(f'{self.taskname} streamlink args: {streamlink_args}')

        ffmpeg_args = [
            ToolsList.get('ffmpeg'),
            "-i", f"http://localhost:{port}",
            "-c", "copy",
        ]
        if self.segment:
            ffmpeg_args += ['-f','segment',
                            '-segment_time',str(self.segment),
                            '-reset_timestamps','1',
                            raw_name]
        else:
            ffmpeg_args += [raw_name]
        self.logger.debug(f'{self.taskname} ffmpeg args: {ffmpeg_args}')

        with tempfile.TemporaryFile(dir='.temp') as logfile:
            self.streamlink_proc = subprocess.Popen(streamlink_args, stdin=subprocess.PIPE, stdout=logfile, stderr=subprocess.STDOUT)
            # 等待streamlink流开始
            streamlink_ok = False
            for _ in range(60):
                try:
                    logfile.seek(0)
                    stdout = logfile.read().decode('utf8', errors='ignore')
                    if re.findall(r'http://\d+\.\d+\.\d+\.\d+:'+str(port), stdout):
                        streamlink_ok = True
                        break
                except Exception as e:
                    pass
                time.sleep(1)
            if not streamlink_ok:
                logfile.seek(0)
                log = logfile.read().decode('utf8', errors='ignore')
                raise RuntimeError(f'{self.taskname} Streamlink启动失败: {log}')
            
            self.ffmpeg_proc = subprocess.Popen(ffmpeg_args, stdin=subprocess.PIPE, stdout=logfile, stderr=subprocess.STDOUT)
            while not self.stoped:
                if self.streamlink_proc.poll() is not None or self.ffmpeg_proc.poll() is not None:
                    self.logger.debug(f'{self.taskname} Streamlink return {self.streamlink_proc.returncode}, ffmpeg return {self.ffmpeg_proc.returncode}')
                    break

                self._observe_files()
                time.sleep(1)
            
            if not self.stoped and Onair(self.url):
                logfile.seek(0)
                log = logfile.read().decode('utf8', errors='ignore')
                raise RuntimeError(f'{self.taskname} Streamlink 异常退出 {log[-1000:]}.')

    def start(self):
        # 生成一个uuid，用于标记这次录制的文件
        self.uuid = uuid(8)
        self._active_file = None
        self._started_files.clear()
        self._ended_files.clear()
        return self.start_helper()
    
    def stop(self):
        self.stoped = True
        try:
            self.streamlink_proc.kill()
            self.ffmpeg_proc.kill()
        except Exception as e:
            self.logger.debug(e)
        finally:
            out, _ = self.streamlink_proc.communicate(timeout=0.1)
            if out: 
                self.logger.debug(f'{self.taskname} streamlink: {out}')
            out, _ = self.ffmpeg_proc.communicate(timeout=0.1)
            if out: 
                self.logger.debug(f'{self.taskname} streamlink: {out}')

        self._observe_files(final=True)
        
        self.logger.debug('Streamlink downloader stoped.')
