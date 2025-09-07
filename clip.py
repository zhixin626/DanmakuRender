import subprocess, os, json
from pathlib import Path
import tempfile
from typing import List, Optional, Tuple, Dict, Union
import logging

logger = logging.getLogger(__name__)

def _probe_media(ffprobe: str, path: str) -> Dict[str, Union[str, float, int, Tuple[int, int]]]:
    """
    用 ffprobe 获取基本信息：duration（秒，float），分辨率（width,height），
    以及格式/比特率等。字段缺失时做容错。
    """
    # 取视频流宽高 + 容器时长
    cmd = [
        ffprobe,
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-show_entries", "format=duration,bit_rate,format_name",
        "-of", "json",
        path
    ]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
        data = json.loads(out.decode("utf-8", errors="ignore"))
    except subprocess.CalledProcessError as e:
        logger.warning("ffprobe 失败：%s", e.output.decode("utf-8", errors="ignore"))
        data = {}
    except Exception as e:
        logger.warning("解析 ffprobe 输出失败：%s", e)
        data = {}

    width = height = None
    duration = None
    bit_rate = None
    fmt = None

    try:
        s0 = (data.get("streams") or [{}])[0]
        width = s0.get("width")
        height = s0.get("height")
    except Exception:
        pass

    try:
        fmt_dict = data.get("format") or {}
        # ffprobe 的 duration 是字符串
        duration = float(fmt_dict.get("duration")) if fmt_dict.get("duration") else None
        bit_rate = int(fmt_dict.get("bit_rate")) if fmt_dict.get("bit_rate") else None
        fmt = fmt_dict.get("format_name")
    except Exception:
        pass

    return {
        "duration": duration,                 # 秒（float）或 None
        "resolution": (width, height) if width and height else None,
        "width": width,
        "height": height,
        "bit_rate": bit_rate,                 # 整数（bps）或 None
        "format": fmt,                        # 例如 "mov,mp4,m4a,3gp,3g2,mj2"
        "path": str(path),
        "size": os.path.getsize(path) if os.path.exists(path) else None,
    }

def merge_mp4(
    mp4_list: List[str],
    out_path: Optional[str] = None,
    ffmpeg: str = r"tools/ffmpeg.exe",
    ffprobe: str = r"tools/ffprobe.exe",
    return_info: bool = False,   # ← 新增：是否返回媒体信息
) -> Union[str, Tuple[str, Dict[str, Union[str, int, float, Tuple[int, int]]]]]:
    """
    1 个文件：直接返回原路径；
    多个文件：使用 concat demuxer 极速合并（-c copy，不重编码）。
    当 return_info=True 时，返回 (out_path, info_dict)；否则仅返回 out_path。
    """
    inputs = [str(Path(p)) for p in mp4_list if p]
    if not inputs:
        raise ValueError("mp4_list 为空")

    # 输出路径
    if len(inputs) == 1:
        out_path_final = str(Path(inputs[0]).resolve())
        if return_info:
            info = _probe_media(ffprobe, out_path_final)
            return out_path_final, info
        return out_path_final

    if out_path is None:
        out_path = str(Path(inputs[0]).with_name("final.mp4"))
    out_path = str(Path(out_path))

    # —— 生成 filelist（必须是“可被其他进程读取的命名临时文件”）——
    tmp = tempfile.NamedTemporaryFile(
        prefix="ff_filelist_",
        suffix=".txt",
        mode="w",
        encoding="utf-8",
        delete=False,    # Windows 下要先关闭句柄
    )
    try:
        for p in inputs:
            tmp.write(f"file '{Path(p).resolve().as_posix()}'\n")
        tmp.flush()
        filelist = tmp.name
    finally:
        tmp.close()  # 先关闭，让 FFmpeg 能读取

    try:
        cmd = [
            ffmpeg, "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", filelist,
            "-c", "copy",
            "-movflags", "+faststart",
            out_path,
        ]
        # 正确的 logging 用法（占位符），避免你之前遇到的 logging 报错
        logger.debug("merge_mp4 cmd: %s", cmd)

        subprocess.run(cmd, check=True)

        if return_info:
            info = _probe_media(ffprobe, out_path)
            return out_path, info
        return out_path
    finally:
        # 删除临时清单
        try:
            os.remove(filelist)
        except OSError:
            pass

def clip_video(video, start, end, output=None):
    # 用法示例
    # video="./芊芊龍/芊芊龍-2025年07月24日23点20分.flv"
    # clip_video(video, "00:00:00", "00:00:30")
    video_path = Path(video)
    if not output:
        output = video_path.with_name(f"{video_path.stem}_clip{video_path.suffix}")
    def hms_to_seconds(hms):
        h, m, s = map(int, hms.split(":"))
        return h * 3600 + m * 60 + s
    
    duration = hms_to_seconds(end) - hms_to_seconds(start)
    if duration <= 0:
        raise ValueError("end 时间必须大于 start 时间")

    cmd = [
        r"tools/ffmpeg.exe",
        "-y",                    # 覆盖输出
        "-ss", start,            # 开始时间
        "-i", str(video_path),   # 输入文件
        "-t", str(duration),     # 持续时间
        "-c", "copy",            # 不重新编码
        str(output)              # 输出文件
    ]
    subprocess.run(cmd, check=True)
    return str(output)
def flv_to_mp4(
    video: str,
    ffmpeg: str = r"tools/ffmpeg.exe",
    ffprobe: str = r"tools/ffprobe.exe",
) -> str:
    """
    输入:  D:/path/1.flv
    输出:  D:/path/1.mp4 （同名同目录）
    策略:
      - 若 vcodec=h264 且 acodec=aac -> 直接 remux (-c copy)
      - 若 vcodec=h264 且 acodec!=aac -> 复制视频, 音频转aac
      - 其他情况 -> 统一转码为 h264 + aac
    """
    p = Path(video)
    if not p.exists():
        raise FileNotFoundError(video)
    out = p.with_suffix(".mp4")

    # 小工具: 用 ffprobe 取编码名（codec_name）
    def codec(kind: str) -> str:
        # kind: "v:0" or "a:0"
        try:
            res = subprocess.run(
                [
                    ffprobe, "-v", "error",
                    "-select_streams", kind,
                    "-show_entries", "stream=codec_name",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    str(p)
                ],
                capture_output=True, text=True, check=True
            )
            name = (res.stdout.strip().splitlines() or [""])[-1].lower()
            return name
        except Exception:
            return ""

    vcodec = codec("v:0")
    acodec = codec("a:0")

    # 构建命令（build ffmpeg command）
    cmd = [ffmpeg, "-y", "-i", str(p), "-movflags", "+faststart"]
    # 情况 1：h264 + aac -> 直接封装
    if vcodec == "h264" and acodec == "aac":
        cmd += ["-c", "copy"]
    # 情况 2：视频可复制，音频转码到 aac
    elif vcodec == "h264" and acodec not in ("", "aac"):
        cmd += ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k"]
    # 情况 3：其他 -> 全转码到 h264+aac，保证 mp4 兼容
    else:
        cmd += [
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "medium", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k"
        ]

    cmd += [str(out)]
    subprocess.run(cmd, check=True)
    return str(out)
def compute_lines():
    height=1080
    dst=10
    margin_h=10
    dmrate=0.3
    fontsize=60
    _ntracks = int(((height - dst) * dmrate) / (fontsize + margin_h))
    return _ntracks
if __name__ == '__main__': 
    clip_video("./不可一世杀手（弹幕版）/final-2025-9-2-03-25.mp4",
        start="0:00:00",
        end="02:23:12",output="./不可一世杀手（弹幕版）/clip1.mp4")
    clip_video(video="./不可一世杀手（弹幕版）/final-2025-9-2-03-25.mp4",
        start="02:23:26",
        end="04:18:20",output="./不可一世杀手（弹幕版）/clip2.mp4")
    merge_mp4(["./不可一世杀手（弹幕版）/clip1.mp4","./不可一世杀手（弹幕版）/clip2.mp4"],
        )


