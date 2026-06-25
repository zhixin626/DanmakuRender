import copy
import logging
import os
import sys
import subprocess
from os.path import exists, abspath, join, dirname, isdir

from .baserender import BaseRender
from DMR.utils import *

# 项目根目录下的多核 emoji 渲染脚本
_MT_SCRIPT = abspath(join(dirname(__file__), "..", "..", "emoji_render_mt.py"))


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
    多核并行 nv12 流水线。复用项目根目录的 emoji_render_mt.py。
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

    @staticmethod
    def _arg_value(args, key, default=None):
        try:
            return args[args.index(key) + 1]
        except (ValueError, IndexError):
            return default

    def render_one(self, video: VideoInfo, output: str, **kwargs):
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
        self.logger.info(f"emoji 平台={platform or '未知'} 表情包={pack}")

        cmd = [sys.executable, _MT_SCRIPT, video.path, danmaku, output,
               "--workers", str(self.workers), "--segmul", str(self.segmul),
               "--vencoder", self.vencoder, "--vb", str(vb), "--ab", str(ab)]
        if isdir(pack):
            cmd += ["--emoji-pack", pack]
        if self.emoji_font:
            cmd += ["--emoji-font", self.emoji_font]
        if self.output_resize:
            cmd += ["--resize", str(self.output_resize)]
        if not self.hwaccel:
            cmd += ["--no-hwaccel"]

        start_time = datetime.now()
        self.logger.info(f"开始 emoji 渲染: {output}")
        self.logger.debug("emoji 渲染命令: %s", " ".join(cmd))
        self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      encoding="utf-8", errors="replace")
        tail = []
        for line in self._proc.stdout:
            line = line.rstrip("\r\n")
            if line:
                tail.append(line)
                if len(tail) > 30:
                    tail.pop(0)
                if self.debug:
                    self.logger.debug("[emoji] %s", line)
        self._proc.wait()
        rc = self._proc.returncode
        self._proc = None

        if rc == 0 and exists(output):
            output_info: VideoInfo = copy.deepcopy(video)
            output_info.dtype = "dm_video"
            output_info.path = output
            output_info.file_id = uuid()
            output_info.size = os.path.getsize(output)
            output_info.ctime = start_time
            output_info.dm_file_id = None
            output_info.src_video_id = video.file_id
            self.logger.info(f"emoji 渲染完成: {output}")
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
