import json
import logging
import math
import os
import sys
import shutil
import subprocess
import time
from os.path import exists, abspath, join, dirname, isdir, splitext, basename

from .baserender import BaseRender, RenderStopped
from .resolution import resolve_render_canvas
from DMR.utils import *

# 同目录下的多核 emoji 渲染脚本(独立子进程运行,零 DMR 依赖)
_MT_SCRIPT = abspath(join(dirname(__file__), "emoji_render_mt.py"))


from .sprites import detect_platform


class EmojiDmRender(BaseRender):
    """
    弹幕彩色 emoji 渲染引擎(方案2)：libass 烧普通弹幕 + Pillow 渲彩色 emoji/表情包贴图,
    整段单路 nv12 流水线。复用同目录的 emoji_render_mt.py。
    与 DmRender 同接口(render_one/stop),按 mode='emoji_dmrender' 选用。
    该引擎保持为 GPU 不可用时的备用路径；GPU 面板的新功能只在 gpu_render 实现。
    """
    def __init__(self,
                 vencoder: str = "h264_nvenc",
                 vencoder_args: list = None,
                 aencoder: str = "aac",
                 aencoder_args: list = None,
                 output_resize: str = None,
                 rescale_to_1080p: bool = False,
                 rescale_orientation: str = "auto",
                 min_fps: float = None,
                 emoji_pack: str = None,
                 emoji_font: str = None,
                 hwaccel: bool = True,
                 ffmpeg: str = None,
                 debug=False,
                 **kwargs):
        self.vencoder = vencoder or "h264_nvenc"
        self.vencoder_args = vencoder_args or []
        self.aencoder = aencoder or "aac"
        self.aencoder_args = aencoder_args or []
        self.output_resize = output_resize
        self.rescale_to_1080p = bool(rescale_to_1080p)
        self.rescale_orientation = rescale_orientation
        if min_fps is None:
            self.min_fps = None
        else:
            if isinstance(min_fps, bool):
                raise ValueError("min_fps must be a positive number or null")
            self.min_fps = float(min_fps)
            if not math.isfinite(self.min_fps) or self.min_fps <= 0:
                raise ValueError("min_fps must be a positive number or null")
        self.emoji_pack = emoji_pack   # 填了就直接用该路径;留空(None)时默认 ./emoji_pack/<平台>
        self.emoji_font = emoji_font
        self.hwaccel = hwaccel
        self.debug = debug
        self.logger = logging.getLogger(__name__)
        self._proc = None
        self._workdir = None   # emoji_render_mt 的临时目录,stop() 强杀后由本进程代为清理
        self._active_output = None
        self._output_complete = False

    # emoji_render_mt 每行日志形如 "[INFO] xxx",按级别转发到 DMR 主日志。
    # 无前缀的行(ffmpeg 直接吐的东西)一律 debug,保持控制台干净。
    _LEVELS = {"DEBUG": logging.DEBUG, "INFO": logging.INFO,
               "WARNING": logging.WARNING, "ERROR": logging.ERROR, "CRITICAL": logging.CRITICAL}

    def _relay(self, line: str):
        level, msg = logging.DEBUG, line
        if line.startswith("["):
            end = line.find("]")
            if end > 0 and line[1:end] in self._LEVELS:
                level = self._LEVELS[line[1:end]]
                msg = line[end + 1:].strip()
        self.logger.log(level, "[emoji] %s", msg)

    @staticmethod
    def _arg_value(args, key, default=None):
        try:
            return args[args.index(key) + 1]
        except (ValueError, IndexError):
            return default

    def render_one(self, video: VideoInfo, output: str, progress_cb=None, **kwargs):
        if self.is_stopped():
            return False, RenderStopped('用户已停止渲染')
        if not exists(video.path):
            raise RuntimeError(f"不存在视频文件 {video.path}，跳过渲染.")
        render_plan = kwargs.get("render_plan")
        canvas_resolution = kwargs.get("canvas_resolution")
        if canvas_resolution is None:
            canvas_resolution = resolve_render_canvas(
                getattr(video, "resolution", None),
                rescale_to_1080p=self.rescale_to_1080p,
                rescale_orientation=self.rescale_orientation,
            )
        else:
            canvas_resolution = tuple(int(value) for value in canvas_resolution)
        danmaku = kwargs.get("danmaku") or video.dm_file_id
        input_path = render_plan or danmaku
        if not input_path or not exists(input_path):
            raise RuntimeError(f"不存在渲染输入 {input_path}，跳过渲染.")

        valid_output = safe_filename(output)
        if valid_output != output:
            self.logger.warning(f"输出文件名 {output} 不合法或已存在，已更改为 {valid_output}.")
            output = valid_output
        os.makedirs(dirname(output), exist_ok=True)

        vb = self._arg_value(self.vencoder_args, "-b:v", "8M")
        ab = self._arg_value(self.aencoder_args, "-b:a", "160K")

        # 表情包路径:配置填了就直接用;没填则默认 ./emoji_pack/<平台>(与下载/输出的 ./ 相对路径风格统一)
        platform = detect_platform(getattr(video, "dm_file_id", None) or input_path)
        if self.emoji_pack:
            pack = self.emoji_pack
        else:
            pack = join("./emoji_pack", platform) if platform else "./emoji_pack"

        cmd = [sys.executable, _MT_SCRIPT, video.path, input_path, output,
               "--vencoder", self.vencoder, "--aencoder", self.aencoder,
               "--vb", str(vb), "--ab", str(ab)]
        # 与 DmRender 一致：完整透传编码器参数列表。子进程收到 --venc-args/--aenc-args 就用完整列表，
        # 收不到（旧调用/未配置）则回退到上面的 --vb/--ab，保持原有行为不受影响。
        if self.vencoder_args:
            cmd += ["--venc-args", json.dumps(list(self.vencoder_args))]
        if self.aencoder_args:
            cmd += ["--aenc-args", json.dumps(list(self.aencoder_args))]
        if isdir(pack):
            cmd += ["--emoji-pack", pack]
        # 平台 emoticon 与本地表情包是两套来源。即使 pack 目录尚不存在，
        # 也要给平台图片一个可创建、可复用的缓存目录。
        cmd += ["--emoticon-dir", join(pack, "emoticon")]
        if self.emoji_font:
            cmd += ["--emoji-font", self.emoji_font]
        if self.output_resize:
            cmd += ["--resize", str(self.output_resize)]
        if self.rescale_to_1080p:
            cmd += ["--rescale-to-1080p"]
        cmd += [
            "--canvas-resolution",
            f"{canvas_resolution[0]}x{canvas_resolution[1]}",
        ]
        if self.min_fps is not None:
            cmd += ["--min-fps", f"{self.min_fps:g}"]
        if render_plan:
            cmd += ["--render-plan"]
        if not self.hwaccel:
            cmd += ["--no-hwaccel"]

        # 与 emoji_render_mt.main 中的 work 路径保持一致
        self._workdir = join(dirname(output) or ".", "_mt_" + splitext(basename(output))[0])
        self._active_output = output
        self._output_complete = False

        start_time = datetime.now()
        self.logger.debug("开始 emoji 渲染: %s；命令: %s", output, " ".join(cmd))
        tail = []
        success = False
        rc = None
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                encoding="utf-8",
                errors="replace",
            )
            for line in self._proc.stdout:
                line = line.rstrip("\r\n")
                if not line:
                    continue
                # 机读进度行（emoji_render_mt 在被管道捕获时输出）→ 回调百分比，不计入日志 tail
                if line.startswith("[DMR_PROGRESS]"):
                    if progress_cb:
                        try:
                            progress_cb(int(float(line.split()[1])))
                        except Exception:
                            pass
                    continue
                tail.append(line)
                if len(tail) > 30:
                    tail.pop(0)
                self._relay(line)
            self._proc.wait()
            rc = self._proc.returncode
            if self.is_stopped():
                return False, RenderStopped('用户已停止渲染')
            output_ready = rc == 0 and exists(output)

            if output_ready:
                success = True
                self._output_complete = True
                self.logger.debug(f"emoji 渲染完成: {output}")
                return True, self.output_info(video,output,canvas_resolution,start_time)

            err = "emoji 渲染失败(返回码 %s):\n%s" % (rc, "\n".join(tail[-15:]))
            self.logger.error(err)
            return False, RuntimeError(err)
        finally:
            p = self._proc
            if p and p.poll() is None:
                self._terminate_proc(p)
            self._proc = None
            self._clean_workdir()
            if not success:
                self._clean_file(output, "未完成的 emoji 输出")
            self._active_output = None

    def stop(self):
        self._stopped = True
        self.logger.debug("emoji render stop.")
        p = self._proc
        self._terminate_proc(p)
        self._clean_workdir()
        if not self._output_complete:
            self._clean_file(self._active_output, "已停止的 emoji 输出")
        self._active_output = None

    @staticmethod
    def _terminate_proc(p):
        if not p or p.poll() is not None:
            return
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(p.pid)],
                    capture_output=True,
                )
            else:
                p.terminate()
        except Exception:
            pass
        # taskkill /F 让 emoji_render_mt 来不及自清理,且返回时进程未必已消失,
        # 必须等它真退出再删除临时文件。
        try:
            p.wait(timeout=10)
        except Exception:
            pass

    def _clean_workdir(self):
        work = self._workdir
        self._workdir = None
        if not work or not isdir(work):
            return
        for i in range(5):
            try:
                shutil.rmtree(work)
                self.logger.debug("已清理 emoji 渲染临时目录: %s", work)
                return
            except OSError as e:
                if i == 4:
                    self.logger.warning("emoji 渲染临时目录清理失败,请手动删除: %s (%s)", work, e)
                    return
                time.sleep(1)

    def _clean_file(self, path, label):
        if not path or not exists(path):
            return
        for i in range(5):
            try:
                os.remove(path)
                self.logger.debug("已清理%s: %s", label, path)
                return
            except OSError as e:
                if i == 4:
                    self.logger.warning("%s清理失败,请手动删除: %s (%s)", label, path, e)
                    return
                time.sleep(1)
