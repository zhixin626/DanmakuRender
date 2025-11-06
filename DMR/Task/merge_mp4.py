import subprocess, os, json
from pathlib import Path
import tempfile
from typing import List, Optional, Tuple, Dict, Union
import logging
from datetime import datetime
import re
from send2trash import send2trash
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
def get_live_start_time(dmfile_path,start=True):
    try:
        if start:
            txt_path = Path(dmfile_path).parent / "_livestart_times.txt"
        else:
            txt_path = Path(dmfile_path).parent / "_liveend_times.txt"
        with open(txt_path, "r", encoding="utf-8") as f:
            # 取最后一行（strip 去掉换行）
            last_line = None
            for line in f:
                if line.strip():
                    last_line = line.strip()
        if last_line:
            time = datetime.fromisoformat(last_line)
        else:
            time = datetime.now()
    except Exception:
        time = datetime.now()
    return time

def _gen_autoname(file_path: Path) -> Path:
    """生成 11月2日_merged[(-NNN)].mp4 的不重名文件路径"""
    time = get_live_start_time(file_path)
    base_dir=file_path.parent
    fname = f"{time.month}月{time.day}日_merged.mp4"
    cand = base_dir / fname
    if cand.exists():
        i = 1
        while True:
            fname2 = f"{time.month}月{time.day}日{i:03d}_merged.mp4"
            cand2 = base_dir / fname2
            if not cand2.exists():
                cand = cand2
                break
            i += 1
    return cand

def merge_amplify_mp4(
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

    # === 单文件分支
    if len(inputs) == 1:
        src = Path(inputs[0]).resolve()
        base_dir = src.parent

        # 没给 out_path 就用 _gen_autoname(base_dir)
        if not out_path:
            dst = _gen_autoname(src)
        else:
            dst = Path(out_path)

        # 改名
        if src != dst:
            src.rename(dst)
        out_path = str(dst)
        # 进行音频增益至-1dB-----------------------------------------------------
        try:
            amplified = amplify_to_minus1db(out_path)
            send2trash(out_path)                                # 删除放入垃圾桶
            out_path  = amplified
        except Exception as e:
            logger.warning(f"amplify 失败，已跳过: {e}")
        # 进行音频增益至-1dB-----------------------------------------------------

        if return_info:
            info = _probe_media(ffprobe, out_path)
            return out_path, info
        return out_path

    # === 多文件分支
    if out_path is None:
        out_path = str(_gen_autoname( Path(inputs[0]).resolve() ))
    else:
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
            "-loglevel", "error",
            "-safe", "0",
            "-i", filelist,
            "-c", "copy",
            "-movflags", "+faststart",
            out_path,
        ]
        # 正确的 logging 用法（占位符），避免你之前遇到的 logging 报错
        logger.debug("merge_mp4 cmd: %s", cmd)
        subprocess.run(cmd, check=True)

        # 进行音频增益至-1dB-------------------------------------------------
        try:
            amplified = amplify_to_minus1db(out_path)
            send2trash(out_path)                                # 删除放入垃圾桶
            out_path = amplified
        except Exception as e:
            logger.warning(f"amplify 失败，已跳过: {e}")
        # 进行音频增益至-1dB-------------------------------------------------

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

def detect(file:str):
    cmd = [
            'tools/ffmpeg.exe',
            '-hide_banner',
            '-i',file,
            '-vn',
            '-af','volumedetect',
            '-f','null','-'
            ]
    r = subprocess.run(cmd, text=True, capture_output=True ,encoding='utf-8')
    m = re.search(r'max_volume:\s*([-\d\.]+)\s*dB', r.stderr)
    if not m:
        raise RuntimeError("未检测到 max_volume（可能没有音轨或滤镜未运行）")
    return float(m.group(1))

def amplify_to_minus1db(file:str):
    peak = detect(file) # 检测最高音量max_volume
    if peak is None:
        raise RuntimeError("未检测到 max_volume")

    gain_db = -1 - peak
    if gain_db < 0:
        gain_db = 0
    out = Path(file).with_name(Path(file).stem + "_amplified.mp4")
    cmd = [
        'tools/ffmpeg.exe',
        '-i', file,
        '-af', f'volume={gain_db:.2f}dB,alimiter=limit=-1dB',
        '-c:v', 'copy',
        str(out)
    ]
    logger.info('开始进行音频增益')
    result=subprocess.run(cmd, text=True, capture_output=True ,encoding='utf-8')
    logger.info(f"检测峰值 {peak:.2f} dBFS → 放大 {gain_db:.2f} dB → 输出: {out}")
    return str(out)