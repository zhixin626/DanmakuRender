import os
import subprocess
import json
import logging
import warnings
import re
from pathlib import Path
from typing import Union

from .toolsmgr import ToolsList

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


class FFprobe():
    header = {
                'Content-Type': 'application/x-www-form-urlencoded',
                'User-Agent': 'Mozilla/5.0 (Linux; Android 5.0; SM-G900P Build/LRX21T) AppleWebKit/537.36 '
                                '(KHTML, like Gecko) Chrome/75.0.3770.100 Mobile Safari/537.36 '
            }

    @classmethod
    def ffprobe(cls) -> str:
        return ToolsList.get('ffprobe')
    
    @classmethod
    def run_ffprobe(cls,fpath):
        out = subprocess.check_output([
            cls.ffprobe(),
            '-i', fpath,
            '-print_format','json',
            '-select_streams', 'v:0',
            '-show_format','-show_streams',
            '-v','quiet'
            ])
        out = out.decode('utf8')
        res = json.loads(out)
        return res
        
    @classmethod
    def get_duration(cls,fpath) -> float:
        try:
            res = cls.run_ffprobe(fpath)
            # format.duration 本身就是媒体时长，不是结束时间戳。
            # FLV 经常带有非零 start_time；再次相减会把有效时长算成 0。
            duration = float(res['format']['duration'])
            return duration if duration > 0 else -1
        except Exception:
            return -1

    @classmethod
    def get_timeline_duration(cls, fpath) -> float:
        """返回视频包时间线覆盖的真实时长。

        直播 FLV 的 format.duration 可能来自不准确的容器元数据，比最后一个
        视频包的 PTS 提前数秒。渲染器会消费全部视频包，因此布局也必须使用
        同一条媒体时间线。这里只解复用包、不解码画面，开销远小于逐帧探测。
        """
        fallback = cls.get_duration(fpath)
        if not os.path.isfile(fpath):
            return fallback

        fps = cls.get_fps(fpath)
        frame_duration = 1.0 / fps if fps > 0 else 0.0
        cmd = [
            cls.ffprobe(),
            "-v", "error",
            "-select_streams", "v:0",
            "-show_packets",
            "-show_entries", "packet=pts_time,dts_time,duration_time",
            "-of", "csv=p=0",
            fpath,
        ]
        proc = None
        first_timestamp = None
        last_timestamp = None
        packet_count = 0

        def parse_number(raw):
            try:
                return float(raw)
            except (TypeError, ValueError):
                return None

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="ignore",
            )
            for raw_line in proc.stdout:
                fields = raw_line.strip().split(",")
                pts = parse_number(fields[0]) if fields else None
                dts = parse_number(fields[1]) if len(fields) > 1 else None
                packet_duration = (
                    parse_number(fields[2]) if len(fields) > 2 else None
                )
                timestamp = pts if pts is not None else dts
                if timestamp is None:
                    continue
                packet_count += 1
                first_timestamp = (
                    timestamp
                    if first_timestamp is None
                    else min(first_timestamp, timestamp)
                )
                packet_end = timestamp + (
                    packet_duration
                    if packet_duration is not None and packet_duration > 0
                    else frame_duration
                )
                last_timestamp = (
                    packet_end
                    if last_timestamp is None
                    else max(last_timestamp, packet_end)
                )
            returncode = proc.wait()
            if returncode != 0 or first_timestamp is None or last_timestamp is None:
                return fallback

            pts_duration = last_timestamp - first_timestamp
            if pts_duration <= 0:
                return fallback

            # CFR直播源通常同时满足 PTS跨度≈视频包数/标称帧率。取稍大的值，
            # 保证RenderPlan不会比渲染器实际消费的帧时间线更短。
            frame_clock_duration = (
                packet_count / fps if fps > 0 and packet_count > 0 else 0.0
            )
            if frame_clock_duration > 0:
                tolerance = max(
                    0.25,
                    0.02 * max(pts_duration, frame_clock_duration),
                )
                if abs(pts_duration - frame_clock_duration) <= tolerance:
                    pts_duration = max(pts_duration, frame_clock_duration)

            if fallback > 0 and abs(pts_duration - fallback) >= 0.05:
                logger.debug(
                    "媒体时间线时长修正: format=%.3fs, video_pts=%.3fs (%s)",
                    fallback,
                    pts_duration,
                    fpath,
                )
            return pts_duration
        except Exception as exc:
            logger.debug("探测视频包时间线失败 %s: %s", fpath, exc)
            if proc is not None:
                try:
                    proc.kill()
                except Exception:
                    pass
            return fallback

    @classmethod
    def run_ffprobe_livestream(cls, url, header=None):
        if header is None:
            header = cls.header
        out = subprocess.check_output([
            cls.ffprobe(),
            '-headers', ''.join('%s: %s\r\n' % x for x in header.items()),
            '-i', url,
            '-select_streams', 'v:0', 
            '-print_format','json',
            '-show_format','-show_streams',
            '-v','quiet'
            ],
            timeout=15,
        )
        out = out.decode('utf8')
        res = json.loads(out)
        return res

    @classmethod
    def get_livestream_info(cls,url,header=None) -> dict:
        res = cls.run_ffprobe_livestream(url,header)
        return res['streams'][0]
        
    @classmethod
    def get_resolution(cls, url:str, header=None) -> tuple:
        try:
            if url.startswith('http'):
                res = cls.run_ffprobe_livestream(url, header)
            else:
                res = cls.run_ffprobe(url)
            resolution = res['streams'][0]['width'],res['streams'][0]['height']
            return resolution
        except Exception:
            return 0,0

    @classmethod
    def get_fps(cls, url:str, header=None) -> float:
        """取标称帧率(r_frame_rate，如 '60/1' → 60.0)。用 r_frame_rate 而非 avg_frame_rate：
        直播源 avg 常算成 62 这种怪值，r 才是干净的标称值。失败返回 0。"""
        try:
            if url.startswith('http'):
                res = cls.run_ffprobe_livestream(url, header)
            else:
                res = cls.run_ffprobe(url)
            st = res['streams'][0]
            rate = st.get('r_frame_rate') or st.get('avg_frame_rate') or '0'
            num, _, den = str(rate).partition('/')
            den = float(den) if den else 1.0
            return float(num) / den if den else 0.0
        except Exception:
            return 0.0


def source_frame_rate(video_path: str) -> float:
    """Use PyAV's guessed rate, then average rate, like the old renderer."""
    import av

    container = av.open(video_path)
    try:
        stream = container.streams.video[0]
        return float(stream.guessed_rate or stream.average_rate)
    finally:
        container.close()
