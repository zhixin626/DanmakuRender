import copy
import logging
import os

from .baserender import BaseRender
from .ffmpeg import RawFFmpegRender
from .subtitle import escape_sub, generate_subtitle_ass, clean_subtitle_intermediates
from os.path import exists
from DMR.utils import *

class DmRender(BaseRender):
    def __init__(self,
                 hwaccel_args: list,
                 vencoder: str,
                 vencoder_args: list,
                 aencoder: str,
                 aencoder_args: list,
                 output_resize: str,
                 advanced_render_args: dict=None,
                 subtitle: dict=None,
                 ffmpeg: str = None,
                 debug=False,
                 **kwargs
                 ):
        self.hwaccel_args = hwaccel_args if hwaccel_args is not None else []
        self.vencoder = vencoder
        self.vencoder_args = vencoder_args
        self.aencoder = aencoder
        self.aencoder_args = aencoder_args
        self.output_resize = output_resize
        self.advanced_render_args = advanced_render_args if isinstance(advanced_render_args, dict) else {}
        self.subtitle = subtitle if isinstance(subtitle, dict) else {}
        self.ffmpeg = ffmpeg if ffmpeg else ToolsList.get('ffmpeg')
        self.debug = debug

        self.logger = logging.getLogger(__name__)
        self.raw_ffmpeg = RawFFmpegRender(debug=self.debug)

    def render_helper(self, video: str, danmaku: str, output: str, to_stdout: bool = False, logfile=None,
                      duration=None, progress_cb=None):
        ffmpeg_args = [self.ffmpeg, '-y']
        ffmpeg_args += self.hwaccel_args

        if self.output_resize:
            if 'x' in str(self.output_resize):
                scale_args = ['-s', self.output_resize]
            else:
                w, h = FFprobe.get_resolution(video)
                if not (h and w):
                    self.logger.warning(f'获取视频 {video} 分辨率失败, 将使用默认分辨率 1920x1080.')
                    w, h = 1920, 1080
                scale = float(self.output_resize)
                w, h = int(w*scale), int(h*scale)
                scale_args = ['-s', f'{w}x{h}']
        else:
            scale_args = ['-noautoscale']

        danmaku = escape_sub(danmaku)

        # 语音识别字幕：对该视频做 ASR 生成字幕 ass，作为第二层叠加到弹幕之上（共用模块）
        sub_filter = ''
        sub_ass = generate_subtitle_ass(video, self.subtitle, self.logger)
        if sub_ass:
            sub_filter = ",subtitles=filename='%s'" % escape_sub(sub_ass)

        # 自定义video filter
        if self.advanced_render_args.get('filter_complex'):
            filter_name = '-filter_complex'
            filter_str = self.advanced_render_args.get('filter_complex')
            filter_str = replace_keywords(filter_str, {'danmaku': danmaku})
        else:
            filter_name = '-vf'
            # fps 放在 subtitles 之前：先把(可变帧率的直播)源铺成均匀 CFR，再让 libass 逐帧画弹幕
            # → 滚动弹幕匀速顺滑(不再因源 VFR 而抖)。取干净的标称帧率；探测失败则不加(沿用原行为)。
            fps = FFprobe.get_fps(video)
            fps_prefix = f'fps={fps:.6g},' if fps and fps > 0 else ''
            filter_str = '%ssubtitles=filename=\'%s\'' % (fps_prefix, danmaku)

        filter_str += sub_filter   # 弹幕在下、字幕在上，单遍编码一起烧

        ffmpeg_args += [
            '-fflags', '+discardcorrupt+genpts',
            '-analyzeduration', '2147483647', '-probesize', '2147483647',
            '-i', video,
            filter_name, filter_str,

            '-c:v', self.vencoder,
            *self.vencoder_args,
            '-c:a', self.aencoder,
            *self.aencoder_args,
            *scale_args,
            '-video_track_timescale', '90000', # 新加放置合并出现时间戳跳变
            '-ar', '48000', # 新加
            output,
        ]

        self.logger.info(f'开始渲染: {output}')   # 此处才真正开始 ffmpeg 渲染
        result = self.raw_ffmpeg.call_ffmpeg(ffmpeg_args, progress_cb=progress_cb, duration=duration)
        # 渲染成功后，ASR 字幕中间文件由本步自己清理（共用模块），不进主清理管线
        ok = result[0] if isinstance(result, (tuple, list)) else result
        if ok and self.subtitle.get('enable'):
            clean_subtitle_intermediates(video, sub_ass, self.subtitle.get('clean', True), self.logger)
        return result

    def render_one(self, video: VideoInfo, output: str, progress_cb=None, **kwargs):
        if not exists(video.path):
            raise RuntimeError(f'不存在视频文件 {video.path}，跳过渲染.')
        danmaku = kwargs.get('danmaku') or video.dm_file_id
        if not danmaku or not exists(danmaku):
            raise RuntimeError(f'不存在弹幕文件 {danmaku}，跳过渲染.')

        valid_output = safe_filename(output)
        if valid_output != output:
            self.logger.warning(f'输出文件名 {output} 不合法或已存在，已更改为 {valid_output}.')
            output = valid_output

        start_time = datetime.now()
        status, info = self.render_helper(video.path, danmaku, output,
                                          duration=getattr(video, 'duration', None),
                                          progress_cb=progress_cb)
        if status:
            output_info:VideoInfo = copy.deepcopy(video)
            output_info.dtype = 'dm_video'
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
