import os
import io
import platform
import signal
import sys
import subprocess
import logging
import tempfile

from .baserender import BaseRender
from os.path import exists
from DMR.utils import *

class RawFFmpegRender(BaseRender):
    def __init__(self,
                 debug=False,
                 **kwargs
                 ):
        self.debug = debug
        self.logger = logging.getLogger(__name__)

    def call_ffmpeg(self, cmds, progress_cb=None, duration=None, **kwargs):
        ffmpeg_args = [str(x) for x in cmds]
        live_progress = bool(progress_cb) and bool(duration) and duration > 0
        if live_progress:
            # 让 ffmpeg 把机读进度写到 stdout（pipe:1），由 _read_progress 解析成百分比
            ffmpeg_args = [ffmpeg_args[0], '-progress', 'pipe:1', '-nostats'] + ffmpeg_args[1:]
        self.logger.debug(f'ffmpeg render args: {ffmpeg_args}')

        with tempfile.TemporaryFile() as logfile:
            if self.debug:
                self.render_proc = subprocess.Popen(
                    ffmpeg_args, stdin=sys.stdin, stdout=sys.stdout, stderr=subprocess.STDOUT, bufsize=10**8)
                self.render_proc.wait()
                return True, ''
            elif live_progress:
                # stdout 留给 -progress 机读进度，ffmpeg 日志(含 video: 汇总行)走 stderr→logfile
                self.render_proc = subprocess.Popen(
                    ffmpeg_args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=logfile, bufsize=10**6)
                self._read_progress(self.render_proc.stdout, progress_cb, duration)
                self.render_proc.wait()
            else:
                self.render_proc = subprocess.Popen(
                    ffmpeg_args, stdin=subprocess.PIPE, stdout=logfile, stderr=subprocess.STDOUT, bufsize=10**8)
                self.render_proc.wait()

            info = None
            log = ''
            logfile.seek(0)
            for line in logfile.readlines():
                line = line.decode('utf-8', errors='ignore').strip()
                log += line + '\n'
                if 'video:' in line:
                    info = line

            if info:
                return True, info
            else:
                return False, log

    def _read_progress(self, pipe, cb, duration):
        """逐行读 ffmpeg 的 -progress 输出，把 out_time 折算成百分比回调给 cb(0~100)。
        out_time_us/out_time_ms 都是微秒（ffmpeg 的历史命名），除以 1e6 得秒。"""
        last = -1
        try:
            for raw in io.TextIOWrapper(pipe, encoding='utf-8', errors='ignore'):
                line = raw.strip()
                if line.startswith('out_time_us=') or line.startswith('out_time_ms='):
                    val = line.split('=', 1)[1]
                    if not val.isdigit():
                        continue
                    pct = max(0, min(99, int(int(val) / 1_000_000 / duration * 100)))
                    if pct != last:
                        last = pct
                        try:
                            cb(pct)
                        except Exception:
                            pass
                elif line == 'progress=end':
                    try:
                        cb(100)
                    except Exception:
                        pass
        except Exception:
            pass
            
    def render_one(self, cmds, **kwargs):
        start_time = datetime.now()
        status, info = self.call_ffmpeg(cmds, **kwargs)
        if status:
            output_info:VideoInfo = video.copy()
            output_info.path = output
            output_info.file_id = uuid()
            output_info.size = os.path.getsize(output)
            output_info.ctime = start_time
            output_info.src_video_id = video.file_id
            return status, output_info
        else:
            return status, info
            
    def stop(self):
        try:
            out, _ = self.render_proc.communicate(b'q', timeout=5)
            self.logger.debug(out)
        except subprocess.TimeoutExpired:
            self.render_proc.kill()
        except Exception as e:
            self.logger.debug(e)
