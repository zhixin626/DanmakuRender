import copy
import json
import logging
import os
import sys
import shutil
import subprocess
import time
from os.path import exists, abspath, join, dirname, isdir, splitext, basename

from .baserender import BaseRender
from DMR.utils import *

# 同目录下的多核 emoji 渲染脚本(独立子进程运行,零 DMR 依赖)
_MT_SCRIPT = abspath(join(dirname(__file__), "emoji_render_mt.py"))


def _detect_platform(ass_path):
    """从 ASS 头(Title 里的直播URL)判断平台,用于选 emoji_pack 子目录"""
    try:
        head = open(ass_path, encoding="utf-8").read(2000)
        if "bilibili.com" in head: return "bilibili"
        if "douyin.com" in head: return "douyin"
        if "douyu.com" in head: return "douyu"
        if "huya.com" in head: return "huya"
    except Exception:
        pass
    return ""


class EmojiDmRender(BaseRender):
    """
    弹幕彩色 emoji 渲染引擎(方案2)：libass 烧普通弹幕 + Pillow 渲彩色 emoji/表情包贴图,
    多核并行 nv12 流水线。复用同目录的 emoji_render_mt.py。
    与 DmRender 同接口(render_one/stop),按 mode='emoji_dmrender' 选用。
    """
    def __init__(self,
                 vencoder: str = "h264_nvenc",
                 vencoder_args: list = None,
                 aencoder: str = "aac",
                 aencoder_args: list = None,
                 output_resize: str = None,
                 emoji_pack: str = None,
                 emoji_font: str = None,
                 workers: int = 3,
                 segmul: int = 3,
                 hwaccel: bool = True,
                 ffmpeg: str = None,
                 debug=False,
                 **kwargs):
        self.vencoder = vencoder or "h264_nvenc"
        self.vencoder_args = vencoder_args or []
        self.aencoder = aencoder or "aac"
        self.aencoder_args = aencoder_args or []
        self.output_resize = output_resize
        self.emoji_pack = emoji_pack   # 填了就直接用该路径;留空(None)时默认 ./emoji_pack/<平台>
        self.emoji_font = emoji_font
        self.workers = int(workers)
        self.segmul = int(segmul)
        self.hwaccel = hwaccel
        self.debug = debug
        self.logger = logging.getLogger(__name__)
        self._proc = None
        self._workdir = None   # emoji_render_mt 的临时目录,stop() 强杀后由本进程代为清理

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
        if not exists(video.path):
            raise RuntimeError(f"不存在视频文件 {video.path}，跳过渲染.")
        danmaku = kwargs.get("danmaku") or video.dm_file_id
        if not danmaku or not exists(danmaku):
            raise RuntimeError(f"不存在弹幕文件 {danmaku}，跳过渲染.")

        valid_output = safe_filename(output)
        if valid_output != output:
            self.logger.warning(f"输出文件名 {output} 不合法或已存在，已更改为 {valid_output}.")
            output = valid_output
        os.makedirs(dirname(output), exist_ok=True)

        vb = self._arg_value(self.vencoder_args, "-b:v", "8M")
        ab = self._arg_value(self.aencoder_args, "-b:a", "160K")

        # 表情包路径:配置填了就直接用;没填则默认 ./emoji_pack/<平台>(与下载/输出的 ./ 相对路径风格统一)
        platform = _detect_platform(danmaku)
        if self.emoji_pack:
            pack = self.emoji_pack
        else:
            pack = join("./emoji_pack", platform) if platform else "./emoji_pack"

        cmd = [sys.executable, _MT_SCRIPT, video.path, danmaku, output,
               "--workers", str(self.workers), "--segmul", str(self.segmul),
               "--vencoder", self.vencoder, "--aencoder", self.aencoder,
               "--vb", str(vb), "--ab", str(ab)]
        # 与 DmRender 一致：完整透传编码器参数列表。子进程收到 --venc-args/--aenc-args 就用完整列表，
        # 收不到（旧调用/未配置）则回退到上面的 --vb/--ab，保持原有行为不受影响。
        if self.vencoder_args:
            cmd += ["--venc-args", json.dumps(list(self.vencoder_args))]
        if self.aencoder_args:
            cmd += ["--aenc-args", json.dumps(list(self.aencoder_args))]
        if isdir(pack):
            cmd += ["--emoji-pack", pack,
                    "--emoticon-dir", join(pack, "emoticon")]
        if self.emoji_font:
            cmd += ["--emoji-font", self.emoji_font]
        if self.output_resize:
            cmd += ["--resize", str(self.output_resize)]
        if not self.hwaccel:
            cmd += ["--no-hwaccel"]

        # 与 emoji_render_mt.main 中的 work 路径保持一致
        self._workdir = join(dirname(output) or ".", "_mt_" + splitext(basename(output))[0])

        start_time = datetime.now()
        self.logger.debug("开始 emoji 渲染: %s；命令: %s", output, " ".join(cmd))
        self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      encoding="utf-8", errors="replace")
        tail = []
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
        self._proc = None
        # 正常跑完由 emoji_render_mt 自己清理(失败时它会故意保留供排查),这里不再插手
        self._workdir = None

        if rc == 0 and exists(output):
            output_info: VideoInfo = copy.deepcopy(video)
            output_info.dtype = "dm_video"
            output_info.path = output
            output_info.file_id = uuid()
            output_info.size = os.path.getsize(output)
            output_info.ctime = start_time
            output_info.dm_file_id = None
            output_info.src_video_id = video.file_id
            self.logger.debug(f"emoji 渲染完成: {output}")
            return True, output_info
        else:
            err = "emoji 渲染失败(返回码 %s):\n%s" % (rc, "\n".join(tail[-15:]))
            self.logger.error(err)
            return False, err

    def stop(self):
        self.logger.debug("emoji render stop.")
        p = self._proc
        if p and p.poll() is None:
            try:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
                else:
                    p.terminate()
            except Exception:
                pass
            # taskkill /F 让 emoji_render_mt 来不及自清理,且返回时进程未必已消失,
            # 这里等它真退出再删,否则 Windows 上文件还被句柄占着
            try:
                p.wait(timeout=10)
            except Exception:
                pass
        self._clean_workdir()

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
