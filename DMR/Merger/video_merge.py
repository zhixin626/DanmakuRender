"""视频合并的无状态工具函数（原 DMR/utils/video_merge.py 瘦身搬迁）。

分工：这里只有"探测/分组/改写时间轴/纯拼接/响度"这些积木，全部不碰源文件、不删任何东西；
编排（分组执行、直通复制、模板命名、统一删源）在 Merger 类的 _merge_pipeline 里。

- probe_seg           : 探测单段的分辨率 / timescale / 时长 / 编码格式（只读）。
- group_by_format     : 按「连续同分辨率、同编码格式」把探测结果切组。
- retime              : 无损改写 timescale（-c copy，不重编码）。
- concat_copy         : concat 解复用器 + -c copy 纯拼接，输入须已处理好（同格式同 timescale）。
- amplify_mp4 / detect: 音频峰值检测 + 响度增益。

分辨率或编码格式（编码器、像素格式、参数头、音频参数）不一致的段不重编码，
由 Merger 分组各自合并、单段直通复制，例如 GPU 渲染段与回退引擎渲染段。
"""
import os
import re
import json
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Union

from send2trash import send2trash
from DMR.utils.utils import safe_filename

logger = logging.getLogger(__name__)


# 流参数里决定 concat -c copy 能否拼接的部分。参数头(extradata：SPS/PPS 等)
# 不同的段拼在一起，播放器会拿第一段的参数头解后面的段 → 花屏、画面冻住、声音照常。
# timescale 不在其中：不同时由 retime 无损对齐。
VIDEO_FORMAT_KEYS = ("codec_name", "profile", "pix_fmt", "extradata_hash")
AUDIO_FORMAT_KEYS = ("codec_name", "sample_rate", "channels")


def probe_seg(path: Union[str, Path], ffprobe: str = r"ffprobe") -> Dict:
    """探测单个片段用于合并判定的关键信息：
    分辨率 wh=(w,h)、时长 dur(秒)、时间刻度 ts(time_base 分母)、
    编码格式 fmt(VIDEO_FORMAT_KEYS + AUDIO_FORMAT_KEYS 的值；探测失败为 None)。"""
    cmd = [
        ffprobe, "-v", "error", "-show_data_hash", "sha256",
        "-show_entries",
        "stream=codec_type,width,height,time_base,"
        + ",".join(sorted(set(VIDEO_FORMAT_KEYS + AUDIO_FORMAT_KEYS))),
        "-show_entries", "format=duration",
        "-of", "json", str(path),
    ]
    res = {"path": Path(path), "wh": None, "dur": 0.0, "ts": None, "fmt": None}
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
        data = json.loads(out.decode("utf-8", errors="ignore"))
        streams = data.get("streams") or []
        video = next((s for s in streams if s.get("codec_type") == "video"), {})
        audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
        w, h = video.get("width"), video.get("height")
        res["wh"] = (w, h) if (w and h) else None
        tb = video.get("time_base")                    # 形如 "1/90000"
        if tb and "/" in tb:
            den = tb.split("/")[1]
            res["ts"] = int(den) if den.isdigit() else None
        if video:
            res["fmt"] = tuple(video.get(k) for k in VIDEO_FORMAT_KEYS) + tuple(
                audio.get(k) for k in AUDIO_FORMAT_KEYS
            )
        dur = (data.get("format") or {}).get("duration")
        res["dur"] = float(dur) if dur else 0.0
    except Exception as e:
        logger.warning("探测片段失败 %s：%s", path, e)
    return res


def describe_format(meta: Dict) -> str:
    """一段的格式摘要，供日志/确认表区分不能拼接的段。"""
    if not meta["wh"] or not meta["fmt"]:
        return "探测失败"
    codec, profile, pix_fmt, extradata = meta["fmt"][:4]
    return (
        f"{meta['wh'][0]}x{meta['wh'][1]} {codec} {profile or ''} {pix_fmt} "
        f"参数头 {str(extradata).split(':')[-1][:8]}"
    )


def group_by_format(metas: List[Dict]) -> List[List[Dict]]:
    """把 probe_seg 的结果按「连续同分辨率、同编码格式」切成若干组（保持原顺序），
    只有同组的段才能 -c copy 拼接。探测失败的段自成一组：不猜它和谁一致。"""
    groups: List[List[Dict]] = []
    for m in metas:
        key = (m["wh"], m["fmt"])
        if None not in key and groups and (groups[-1][-1]["wh"], groups[-1][-1]["fmt"]) == key:
            groups[-1].append(m)
        else:
            groups.append([m])
    return groups


def retime(src: Union[str, Path], dst: Union[str, Path],
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


def concat_copy(inputs: List[Union[str, Path]], out_path: str, ffmpeg: str = r"ffmpeg"):
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
