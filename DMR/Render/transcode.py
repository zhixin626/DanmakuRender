import copy
import os
import platform
import sys
import subprocess
import logging
import tempfile

from os.path import exists
from .ffmpeg import RawFFmpegRender
from .baserender import BaseRender
from DMR.utils import *


class Transcoder(BaseRender):
    def __init__(self,
                 hwaccel,
                 vencoder: str,
                 vencoder_args: list,
                 aencoder: str,
                 aencoder_args: list,
                 output_resize: str,
                 ffmpeg: str = None,
                 debug=False,
                 **kwargs
                 ):
        hwaccel_value = 'auto' if hwaccel is True else str(hwaccel)
        self.hwaccel_args = (
            ['-hwaccel', hwaccel_value]
            if hwaccel not in (None, False, '', 'false', 'off', 'none')
            else []
        )
        self.vencoder = vencoder
        self.vencoder_args = vencoder_args
        self.aencoder = aencoder
        self.aencoder_args = aencoder_args
        self.output_resize = output_resize
        self.ffmpeg = ffmpeg if ffmpeg else ToolsList.get('ffmpeg')
        self.debug = debug

        self.logger = logging.getLogger(__name__)
        self.raw_ffmpeg = RawFFmpegRender(debug=self.debug)

    def render_helper(self, video: str, output: str, duration=None, progress_cb=None):
        ffmpeg_args = [self.ffmpeg, '-y']
        ffmpeg_args += self.hwaccel_args

        if self.output_resize:
            if 'x' in str(self.output_resize):
                scale_args = ['-s', self.output_resize]
            else:
                w, h = FFprobe.get_resolution(video)
                if not (h and w):
                    self.logger.warn(f'获取视频 {video} 分辨率失败, 将使用默认分辨率 1920x1080.')
                    w, h = 1920, 1080
                scale = float(self.output_resize)
                w, h = int(w*scale), int(h*scale)
                scale_args = ['-s', f'{w}x{h}']
        else:
            scale_args = []
        
        ffmpeg_args += [
            '-fflags', '+discardcorrupt',
            '-i', video,
            '-c:v', self.vencoder,
            *self.vencoder_args,
            '-c:a', self.aencoder,
            *self.aencoder_args,
            *scale_args,
            output,
        ]

        self.logger.info(f'开始转码: {output}')
        return self.raw_ffmpeg.call_ffmpeg(ffmpeg_args, progress_cb=progress_cb, duration=duration)

    def render_one(self, video: VideoInfo, output: str, progress_cb=None, **kwargs):
        if not exists(video.path):
            raise RuntimeError(f'不存在视频文件 {video}，跳过渲染.')
        valid_output = safe_filename(output)
        if valid_output != output:
            self.logger.warn(f'输出文件名 {output} 不合法或已存在，已更改为 {valid_output}.')
            output = valid_output

        start_time = datetime.now()
        status, info = self.render_helper(video.path, output,
                                          duration=getattr(video, 'duration', None),
                                          progress_cb=progress_cb)
        if status:
            output_info:VideoInfo = copy.deepcopy(video)
            output_info.path = output
            output_info.file_id = uuid()
            output_info.size = os.path.getsize(output)
            output_info.ctime = start_time
            output_info.dm_file_id = None
            output_info.src_video_id = video.file_id
            return status, output_info
        else:
            return status, info

    def stop(self):
        self.logger.debug('ffmpeg render stop.')
        self.raw_ffmpeg.stop()
