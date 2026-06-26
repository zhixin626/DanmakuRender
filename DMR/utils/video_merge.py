"""视频合并 / 转码 / 响度处理核心（原 merge_mp4.py）。

- merge_mp4 : 多段 mp4 合并（参数一致走无损 concat，不一致时以最长片段为基准最小化重编码）。
- amplify_mp4 / detect : 音频峰值检测 + 响度增益。
- probe_media : ffprobe 取时长/分辨率等基本信息。
被 Merger 插件以及 merge_only / run_merge / upload_only 等工具调用。
"""
import os
import re
import json
import tempfile
import logging
import subprocess
from pathlib import Path
from functools import lru_cache
from typing import List, Dict, Union

from send2trash import send2trash
from DMR.utils.utils import safe_filename

logger = logging.getLogger(__name__)


def probe_media(
    path: Union[str, Path],
    ffprobe: str = r"ffprobe"
    ):
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
        str(path)
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
        duration_raw = fmt_dict.get("duration")
        duration = float(duration_raw) if isinstance(duration_raw, str) else None
        bit_rate_raw = fmt_dict.get("bit_rate")
        bit_rate = int(bit_rate_raw) if isinstance(bit_rate_raw, str) else None
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


def _concat_copy(inputs: List[Path], out_path: str, ffmpeg: str = r"ffmpeg"):
    """用 concat 解复用器 + -c copy 无损拼接（要求所有片段编码参数一致）。"""
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
            # .resolve()=====转成绝对路径 消掉 . 和 ..
            # .as_posix()====posix风格 用斜杠/而不是反斜杠\
        tmp.flush()
        filelist = tmp.name
    finally:
        tmp.close()  # 先关闭，让 FFmpeg 能读取

    try:
        cmd = [
            ffmpeg, "-y",
            "-f", "concat",
            "-loglevel", "error",
            "-safe", "0",
            "-i", filelist,
            "-c", "copy",
            "-reset_timestamps", "1",
            "-avoid_negative_ts", "make_zero",   # 拼接处时间戳归零，避免非单调 DTS
            "-movflags", "+faststart",
            out_path,
        ]
        subprocess.run(cmd, check=True)
    finally:
        os.remove(filelist)
    return out_path


def _x264_profile(ff_profile: str):
    """把 ffprobe 的 profile 字符串映射成 libx264 的 -profile:v 取值。"""
    if not ff_profile:
        return None
    p = ff_profile.lower()
    if "high 10" in p or "high10" in p:
        return "high10"
    if "high 4:2:2" in p or "high422" in p:
        return "high422"
    if "high 4:4:4" in p or "high444" in p:
        return "high444"
    if "high" in p:
        return "high"
    if "main" in p:
        return "main"
    if "baseline" in p:
        return "baseline"
    return None


def _probe_seg(path: Union[str, Path], ffprobe: str = r"ffprobe"):
    """探测单个片段用于合并判定的关键信息：
    分辨率 (w,h)、时长(秒)、时间刻度 timescale(time_base 分母)、profile、pix_fmt。
    concat -c copy 要求所有片段的「分辨率」和「timescale」都一致，否则会画面错乱或时间轴被拉伸。"""
    cmd = [
        ffprobe, "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,time_base,profile,pix_fmt",
        "-show_entries", "format=duration",
        "-of", "json", str(path),
    ]
    res = {"path": Path(path), "wh": None, "dur": 0.0,
           "ts": None, "profile": None, "pix_fmt": None}
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
        data = json.loads(out.decode("utf-8", errors="ignore"))
        s0 = (data.get("streams") or [{}])[0]
        w, h = s0.get("width"), s0.get("height")
        res["wh"] = (w, h) if (w and h) else None
        tb = s0.get("time_base")                       # 形如 "1/90000"
        if tb and "/" in tb:
            den = tb.split("/")[1]
            res["ts"] = int(den) if den.isdigit() else None
        res["profile"] = _x264_profile(s0.get("profile"))
        res["pix_fmt"] = s0.get("pix_fmt") or "yuv420p"
        dur = (data.get("format") or {}).get("duration")
        res["dur"] = float(dur) if dur else 0.0
    except Exception as e:
        logger.warning("探测片段失败 %s：%s", path, e)
    return res


@lru_cache(maxsize=4)
def _nvenc_available(ffmpeg: str = r"ffmpeg") -> bool:
    """检测当前 ffmpeg 是否带 NVIDIA NVENC H.264 硬件编码器（结果缓存）。"""
    try:
        out = subprocess.check_output(
            [ffmpeg, "-hide_banner", "-encoders"], stderr=subprocess.STDOUT)
        return b"h264_nvenc" in out
    except Exception as e:
        logger.warning("检测 NVENC 失败，将使用软件编码：%s", e)
        return False


def _rescale_to(src: Union[str, Path], dst: Union[str, Path],
                target_w: int, target_h: int, profile: str, pix_fmt: str,
                timescale: int, ffmpeg: str = r"ffmpeg", hw_encode: bool = True):
    """把单个片段重编码到 target_w x target_h（用于分辨率不一致的片段）。
    保持宽高比缩放后补边（同宽高比时无黑边、不拉伸），并对齐编码参数与 timescale。
    hw_encode=True 且检测到 NVENC 时走 GPU 硬件编码，失败自动回退 libx264 软件编码。"""
    vf = (f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease:flags=lanczos,"
          f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2,setsar=1")

    def _build(venc_args):
        cmd = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(src),
            "-vf", vf,
            *venc_args,
            "-pix_fmt", pix_fmt or "yuv420p",
        ]
        if profile:
            cmd += ["-profile:v", profile]
        cmd += [
            "-c:a", "copy",                            # 音频不动，省资源
            "-video_track_timescale", str(timescale),  # 对齐目标 timescale，避免时间轴拉伸
            "-movflags", "+faststart",
            str(dst),
        ]
        return cmd

    # NVENC：恒定质量 VBR，几乎不占 CPU；cq 越小画质越高。
    # -bf 0 关闭 B 帧，使 DTS=PTS，避免与后续片段拼接时出现非单调 DTS（拼接处卡顿）。
    nvenc_args = ["-c:v", "h264_nvenc", "-preset", "p4", "-tune", "hq",
                  "-rc", "vbr", "-cq", "21", "-b:v", "0", "-bf", "0"]
    x264_args  = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-bf", "0"]

    if hw_encode and _nvenc_available(ffmpeg):
        try:
            subprocess.run(_build(nvenc_args), check=True)
            return dst
        except subprocess.CalledProcessError as e:
            logger.warning("NVENC 硬件编码失败，回退 libx264 软件编码：%s", e)
    subprocess.run(_build(x264_args), check=True)
    return dst


def _retime(src: Union[str, Path], dst: Union[str, Path],
            timescale: int, ffmpeg: str = r"ffmpeg"):
    """仅修正 timescale（分辨率已一致、时间刻度不同时使用）。
    -c copy 不重编码，只重写时间戳，几乎不耗资源。"""
    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(src),
        "-c", "copy",
        "-video_track_timescale", str(timescale),
        "-movflags", "+faststart",
        str(dst),
    ]
    subprocess.run(cmd, check=True)
    return dst


def merge_mp4(
    mp4_list: List[str],
    remover=True,
    ffmpeg: str = r"ffmpeg",
    ffprobe: str = r"ffprobe",
    hw_encode: bool = True,
    out_path: str = None,
    ):
    inputs = [Path(p) for p in mp4_list if p]
    if not inputs:
        raise ValueError("mp4_list 为空")

    fist_file = Path(inputs[0])
    # out_path 显式指定输出路径（如编辑器选定的文件名）；否则沿用 <首个文件名>_merged.mp4
    if out_path:
        out_path = safe_filename(str(out_path))
    else:
        out_path = safe_filename(str(fist_file.parent/f"{fist_file.stem}_merged.mp4"))

    # === 单文件分支
    if len(inputs) == 1:
        fist_file.rename(out_path)
        return str(out_path)

    # === 多文件分支：先分析每个片段的分辨率 + timescale ===
    metas = [_probe_seg(p, ffprobe) for p in inputs]

    resolutions = {m["wh"] for m in metas if m["wh"]}
    timescales  = {m["ts"] for m in metas if m["ts"]}

    # 分辨率与 timescale 都一致（或都探测不到）→ 走原来的无损快速通道
    if len(resolutions) <= 1 and len(timescales) <= 1:
        result = _concat_copy(inputs, out_path, ffmpeg)
        if remover:
            for video in inputs:
                send2trash(str(video))
        return result

    # === 存在不一致：以「最长的那个片段」为基准，最大化无损拷贝、最小化重编码 ===
    # 选基准：累计时长最长的分辨率为目标分辨率；该分辨率里最长的片段决定 timescale。
    # 这样多小时的主体素材原样拷贝，只处理少数（通常更短的）异常片段。
    dur_by_res: Dict[tuple, float] = {}
    for m in metas:
        if m["wh"]:
            dur_by_res[m["wh"]] = dur_by_res.get(m["wh"], 0.0) + m["dur"]
    target_wh = max(dur_by_res, key=dur_by_res.get)
    target_w, target_h = target_wh

    in_group = [m for m in metas if m["wh"] == target_wh]
    ref = max(in_group, key=lambda m: m["dur"])         # 该分辨率里最长的片段
    target_ts = ref["ts"] or (max(timescales) if timescales else 90000)
    profile, pix_fmt = ref["profile"], ref["pix_fmt"]

    n_rescale = sum(1 for m in metas if m["wh"] and m["wh"] != target_wh)
    n_retime  = sum(1 for m in metas
                    if m["wh"] == target_wh and m["ts"] and m["ts"] != target_ts)
    encoder = "NVENC硬件编码" if (hw_encode and _nvenc_available(ffmpeg)) else "libx264软件编码"
    logger.warning(
        "片段参数不一致（分辨率 %s，timescale %s）：统一到 %dx%d @ ts=%d。"
        "重编码 %d 个分辨率不符的片段（%s），无损改写时间轴 %d 个，其余 %d 个原样拷贝。",
        sorted(resolutions), sorted(timescales), target_w, target_h, target_ts,
        n_rescale, encoder, n_retime, len(metas) - n_rescale - n_retime,
    )

    tmpdir = tempfile.mkdtemp(prefix="ff_norm_")
    temp_files: List[Path] = []
    try:
        concat_inputs: List[Path] = []
        for i, m in enumerate(metas):
            if not m["wh"]:
                concat_inputs.append(m["path"])               # 探测不到，只能原样拼
            elif m["wh"] != target_wh:
                dst = Path(tmpdir) / f"scale_{i:04d}.mp4"      # 分辨率不符：重编码（较少见、较短）
                _rescale_to(m["path"], dst, target_w, target_h,
                            profile, pix_fmt, target_ts, ffmpeg, hw_encode)
                concat_inputs.append(dst)
                temp_files.append(dst)
            elif m["ts"] and m["ts"] != target_ts:
                dst = Path(tmpdir) / f"retime_{i:04d}.mp4"     # 仅 timescale 不符：无损改写
                _retime(m["path"], dst, target_ts, ffmpeg)
                concat_inputs.append(dst)
                temp_files.append(dst)
            else:
                concat_inputs.append(m["path"])               # 完全一致：原样拷贝

        _concat_copy(concat_inputs, out_path, ffmpeg)

        if remover:
            for video in inputs:                              # 只删真正的输入，临时文件单独清理
                send2trash(str(video))
        return str(out_path)
    finally:
        # 清理所有中间产物，保证输入/输出之外不残留多余文件
        for t in temp_files:
            try:
                os.remove(t)
            except OSError:
                pass
        try:
            os.rmdir(tmpdir)
        except OSError:
            pass


def detect(file: str):
    cmd = [
            'ffmpeg',
            '-hide_banner',
            '-i', file,
            '-vn',  # video none 禁用视频流，不处理视频
            '-af', 'volumedetect',
            '-f', 'null',  # null muxer 不真正产生文件，所有输出数据都直接丢弃
            '-',  # 表示输出到 stdout（标准输出） 真正的媒体数据 → stdout
            ]
    r = subprocess.run(cmd, text=True, capture_output=True, encoding='utf-8')
    m = re.search(r'max_volume:\s*([-\d\.]+)\s*dB', r.stderr)  # 日志 / 消息 / volumedetect 输出 → stderr
    if not m:
        raise RuntimeError("未检测到 max_volume（可能没有音轨或滤镜未运行）")
    return float(m.group(1))


def amplify_mp4(file, target_db=-1, remover=True, extra_gain_db=0) -> Path:
    file = Path(file)
    peak = detect(str(file))  # 检测最高音量max_volume

    gain_db = target_db - peak + extra_gain_db
    if gain_db < 0:
        gain_db = 0

    dst = safe_filename(str(file.parent/f"{file.stem}_amplified.mp4"))
    cmd = [
        'ffmpeg',
        '-y',                # ← 覆盖输出，避免交互
        '-nostdin',          # ← 不读取标准输入 防止 FFmpeg 卡住等待用户输入
        '-hide_banner',
        '-i', str(file),
        '-af', f'volume={gain_db:.2f}dB,alimiter=limit=0.891',  # audio filter音频滤镜
        '-c:v', 'copy',
        '-c:a', 'aac',  # AAC = 高级音频编码（Advanced Audio Coding）
        '-movflags', '+faststart',
        str(dst)
    ]
    logger.info('开始进行音频增益')
    result = subprocess.run(cmd, text=True, capture_output=True, encoding='utf-8')
    if result.returncode != 0:
        logger.error("ffmpeg 失败：%s", result.stderr.strip().splitlines()[-1] if result.stderr else "未知错误")
        raise RuntimeError("ffmpeg 执行失败")
    logger.info(f"检测峰值 {peak:.2f} dBFS → 放大 {gain_db:.2f} dB → 输出: {dst}")
    if remover:
        send2trash(str(file))
    return Path(dst)
