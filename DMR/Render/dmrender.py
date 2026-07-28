import copy
import logging
import os
import platform
import tempfile
from os.path import exists

from .baserender import BaseRender
from .ffmpeg import RawFFmpegRender
from .plan_ass import render_plan_to_ass
from DMR.utils import *


def escape_sub(path):
    if platform.system().lower() == "windows":
        return path.replace("\\", "/").replace(":/", "\\:/")
    return path


class DmRender(BaseRender):
    def __init__(
        self,
        hwaccel,
        vencoder: str,
        vencoder_args: list,
        aencoder: str,
        aencoder_args: list,
        output_resize: str,
        ffmpeg: str = None,
        debug=False,
        **kwargs,
    ):
        hwaccel_value = "auto" if hwaccel is True else str(hwaccel)
        self.hwaccel_args = (
            ["-hwaccel", hwaccel_value]
            if hwaccel not in (None, False, "", "false", "off", "none")
            else []
        )
        self.vencoder = vencoder
        self.vencoder_args = vencoder_args
        self.aencoder = aencoder
        self.aencoder_args = aencoder_args
        self.output_resize = output_resize
        self.ffmpeg = ffmpeg if ffmpeg else ToolsList.get("ffmpeg")
        self.debug = debug
        self.logger = logging.getLogger(__name__)
        self.raw_ffmpeg = RawFFmpegRender(debug=self.debug)

    def render_helper(
        self,
        video: str,
        danmaku: str,
        output: str,
        to_stdout: bool = False,
        logfile=None,
        duration=None,
        progress_cb=None,
    ):
        ffmpeg_args = [self.ffmpeg, "-y", *self.hwaccel_args]
        if self.output_resize:
            if "x" in str(self.output_resize):
                scale_args = ["-s", self.output_resize]
            else:
                w, h = FFprobe.get_resolution(video)
                if not (h and w):
                    self.logger.warning(
                        "获取视频 %s 分辨率失败，将使用默认分辨率 1920x1080.", video
                    )
                    w, h = 1920, 1080
                scale = float(self.output_resize)
                scale_args = ["-s", f"{int(w * scale)}x{int(h * scale)}"]
        else:
            scale_args = ["-noautoscale"]

        danmaku = escape_sub(danmaku)
        filter_name = "-vf"
        fps = FFprobe.get_fps(video)
        fps_prefix = f"fps={fps:.6g}," if fps and fps > 0 else ""
        filter_str = f"{fps_prefix}subtitles=filename='{danmaku}'"

        ffmpeg_args += [
            "-fflags",
            "+discardcorrupt+genpts",
            "-analyzeduration",
            "2147483647",
            "-probesize",
            "2147483647",
            "-i",
            video,
            filter_name,
            filter_str,
            "-c:v",
            self.vencoder,
            *self.vencoder_args,
            "-c:a",
            self.aencoder,
            *self.aencoder_args,
            *scale_args,
            "-video_track_timescale",
            "90000",
            "-ar",
            "48000",
            output,
        ]
        self.logger.info("开始渲染: %s", output)
        return self.raw_ffmpeg.call_ffmpeg(
            ffmpeg_args, progress_cb=progress_cb, duration=duration
        )

    def render_one(self, video: VideoInfo, output: str, progress_cb=None, **kwargs):
        if not exists(video.path):
            raise RuntimeError(f"不存在视频文件 {video.path}，跳过渲染.")

        render_plan = kwargs.get("render_plan")
        danmaku = kwargs.get("danmaku") or video.dm_file_id
        temp_ass = None
        if render_plan:
            if not exists(render_plan):
                raise RuntimeError(f"不存在渲染计划 {render_plan}，跳过渲染.")
            os.makedirs(os.path.dirname(output) or ".", exist_ok=True)
            fd, temp_ass = tempfile.mkstemp(
                prefix="render_plan_",
                suffix=".ass",
                dir=os.path.dirname(output) or None,
            )
            os.close(fd)
            resolution = getattr(video, "resolution", None) or (1920, 1080)
            render_plan_to_ass(
                render_plan,
                temp_ass,
                include_raster_text=True,
                width=int(resolution[0] or 1920),
                height=int(resolution[1] or 1080),
            )
            danmaku = temp_ass
        if not danmaku or not exists(danmaku):
            raise RuntimeError(f"不存在弹幕文件 {danmaku}，跳过渲染.")

        valid_output = safe_filename(output)
        if valid_output != output:
            self.logger.warning(
                "输出文件名 %s 不合法或已存在，已更改为 %s.", output, valid_output
            )
            output = valid_output

        start_time = datetime.now()
        try:
            status, info = self.render_helper(
                video.path,
                danmaku,
                output,
                duration=getattr(video, "duration", None),
                progress_cb=progress_cb,
            )
        finally:
            try:
                if temp_ass and os.path.exists(temp_ass):
                    os.remove(temp_ass)
            except OSError:
                pass

        if not status:
            return status, info
        output_info: VideoInfo = copy.deepcopy(video)
        output_info.dtype = "dm_video"
        output_info.path = output
        output_info.file_id = uuid()
        output_info.size = os.path.getsize(output)
        output_info.ctime = start_time
        output_info.dm_file_id = None
        output_info.src_video_id = video.file_id
        return status, output_info

    def stop(self):
        self.logger.debug("ffmpeg render stop.")
        self.raw_ffmpeg.stop()
