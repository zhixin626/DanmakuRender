"""视频合并的无状态工具函数（原 DMR/utils/video_merge.py 瘦身搬迁）。

分工：这里只有"探测/分组/改写时间轴/纯拼接/响度"这些积木，全部不碰源文件、不删任何东西；
编排（分组执行、直通复制、模板命名、统一删源）在 Merger 类的 _merge_pipeline 里。

- probe_seg           : 探测单段的分辨率 / timescale / 时长 / 编码格式（只读）。
- group_by_format     : 按「连续同分辨率、同编码格式」把探测结果切组。
- split_by_duration   : 再把每组按总时长上限切开（默认 10 小时一个产物）。
- retime              : 无损改写 timescale（-c copy，不重编码）。
- concat_copy         : concat 解复用器 + -c copy 纯拼接，输入须已处理好（同格式同 timescale）。
- concat_with_gain    : 拼接并对整体音频做同一个增益（峰值并行检测，音频与视频同时处理）。
- merge_group         : 一组同格式片段 → 一个产物（retime + 拼接/增益），Merger 与 merge_only 共用。

分辨率或编码格式（编码器、像素格式、参数头、音频参数）不一致的段不重编码，
由 Merger 分组各自合并、单段直通复制，例如 GPU 渲染段与回退引擎渲染段。
"""
import os
import re
import json
import logging
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List, Union


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


# 单个合并产物的默认时长上限：10 小时
DEFAULT_MAX_DURATION = 10 * 3600


def split_by_duration(groups: List[List[Dict]], max_duration: float = DEFAULT_MAX_DURATION) -> List[List[Dict]]:
    """把 group_by_format 的每组再按时长切开，让每个产物总时长不超过 max_duration 秒。
    按顺序往当前组里放段，放下这一段会超限就另起一组；只切在段与段之间，不切开任何一段，
    所以单独一段本身超过上限时自成一组（无法无损再切）。max_duration<=0 表示不限制。"""
    if not max_duration or max_duration <= 0:
        return groups
    result: List[List[Dict]] = []
    for group in groups:
        current, total = [], 0.0
        for m in group:
            dur = m.get("dur") or 0.0
            if current and total + dur > max_duration:
                result.append(current)
                current, total = [], 0.0
            current.append(m)
            total += dur
        if current:
            result.append(current)
    return result


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


class ConcatList:
    """concat 解复用器的文件列表（临时文件，退出时删除）。"""

    def __init__(self, inputs: List[Union[str, Path]]):
        self.inputs = inputs

    def __enter__(self) -> str:
        tmp = tempfile.NamedTemporaryFile(
            prefix="ff_filelist_", suffix=".txt", mode="w", encoding="utf-8",
            delete=False,    # Windows 下要先关闭句柄，FFmpeg 才能读取
        )
        with tmp:
            for p in self.inputs:
                # 绝对路径 + 正斜杠，concat 列表里不需要转义反斜杠
                tmp.write(f"file '{Path(p).resolve().as_posix()}'\n")
        self.path = tmp.name
        return self.path

    def __exit__(self, *_):
        os.remove(self.path)


def concat_copy(inputs: List[Union[str, Path]], out_path: str, ffmpeg: str = r"ffmpeg",
                streams: List[str] = (), faststart: bool = True):
    """用 concat 解复用器 + -c copy 无损拼接（要求所有片段编码参数一致）。
    streams 为额外的 FFmpeg 选择参数，例如 ["-an"] 只拼视频。"""
    with ConcatList(inputs) as filelist:
        cmd = [
            ffmpeg, "-y", "-f", "concat", "-loglevel", "error", "-safe", "0",
            "-i", filelist, *streams, "-c", "copy",
            "-reset_timestamps", "1",
            "-avoid_negative_ts", "make_zero",   # 拼接处时间戳归零，避免非单调 DTS
            *(["-movflags", "+faststart"] if faststart else []),
            out_path,
        ]
        subprocess.run(cmd, check=True)
    return out_path


def detect(file: str):
    """音轨峰值 max_volume（dBFS）。"""
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


def audio_bit_rate(path: Union[str, Path], ffprobe: str = "ffprobe") -> Union[str, None]:
    out = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "a:0", "-show_entries",
         "stream=bit_rate", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    ).stdout.strip()
    return out if out.isdigit() else None


def concat_with_gain(inputs: List[Union[str, Path]], out_path: Union[str, Path],
                     target_db: float = -1, extra_gain_db: float = 0,
                     workdir: Union[str, Path, None] = None, ffmpeg: str = "ffmpeg") -> Path:
    """拼接并对**整体**音频做同一个增益：峰值提到 target_db，再加 extra_gain_db。

    - 增益按全部片段的最高峰值计算（各段峰值的最大值就是整体峰值，各段并行检测）；
    - 拼接后的连续音频只编码一次，各段用同一个增益，接缝处没有单独编码的边界；
    - 视频无损拼接与音频编码同时进行，最后一次封装（只做一次 faststart）。
    音频码率沿用源文件，AAC 用 fast 编码器（约快一倍，高码率下听不出差别）。"""
    workdir = Path(workdir or Path(out_path).parent)
    with ThreadPoolExecutor(max_workers=min(len(inputs), os.cpu_count() or 1)) as pool:
        peak = max(pool.map(detect, [str(p) for p in inputs]))
    gain_db = max(0.0, target_db - peak + extra_gain_db)
    logger.info("音频增益：整体峰值 %.2f dBFS → 放大 %.2f dB", peak, gain_db)

    video = workdir / "gain_video.mp4"
    audio = workdir / "gain_audio.m4a"
    bit_rate = audio_bit_rate(inputs[0])

    def encode_audio():
        with ConcatList(inputs) as filelist:
            subprocess.run(
                [ffmpeg, "-y", "-nostdin", "-loglevel", "error",
                 "-f", "concat", "-safe", "0", "-i", filelist, "-vn",
                 "-af", f"volume={gain_db:.2f}dB,alimiter=limit=0.891",
                 "-c:a", "aac", "-aac_coder", "fast",
                 *(["-b:a", bit_rate] if bit_rate else []), str(audio)],
                check=True,
            )

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [
                pool.submit(concat_copy, inputs, str(video), ffmpeg, ["-an"], False),
                pool.submit(encode_audio),
            ]
            for job in jobs:
                job.result()
        subprocess.run(
            [ffmpeg, "-y", "-nostdin", "-loglevel", "error", "-i", str(video),
             "-i", str(audio), "-map", "0:v", "-map", "1:a", "-c", "copy",
             "-movflags", "+faststart", str(out_path)],
            check=True,
        )
    finally:
        for path in (video, audio):
            if path.exists():
                os.remove(path)
    return Path(out_path)


def merge_group(group: List[Dict], product: Union[str, Path], workdir: Union[str, Path],
                amplify: Union[tuple, None] = None) -> Path:
    """把 group_by_format 的一组（连续、同格式）合成 product：
    timescale 不一致的段先无损 retime；amplify=(target_db, extra_gain_db) 时整体增益，
    否则 -c copy 拼接（单段直接复制）。源文件不改动，临时件放在 workdir 并用完删除。"""
    workdir = Path(workdir)
    reference = max(group, key=lambda item: item.get("dur") or 0)
    target_ts = reference.get("ts")
    inputs, retimed = [], []
    for index, segment in enumerate(group):
        if len(group) > 1 and target_ts and segment.get("ts") and segment["ts"] != target_ts:
            adjusted = workdir / f"retime_{Path(product).stem}_{index:03d}.mp4"
            retime(segment["path"], adjusted, target_ts)
            inputs.append(adjusted)
            retimed.append(adjusted)
        else:
            inputs.append(segment["path"])
    try:
        if amplify:
            concat_with_gain(inputs, product, *amplify, workdir=workdir)
        elif len(inputs) == 1:
            shutil.copy2(inputs[0], product)
        else:
            concat_copy(inputs, str(product))
    finally:
        for path in retimed:
            os.remove(path)
    return Path(product)
