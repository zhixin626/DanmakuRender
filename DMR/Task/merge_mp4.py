import subprocess, os, json
from pathlib import Path
import tempfile
from typing import List, Optional, Tuple, Dict, Union
import logging
from datetime import datetime
import re
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

    # —— 这里开始是你要的“小修改”：自动命名 final-YYYY-M-D-HH-MM.mp4 —— #
    if out_path is None:
        base_dir = Path(inputs[0]).resolve().parent
        now = datetime.now()
        # 用连字符替代冒号：final-2025-6-17-14-00.mp4
        fname = f"final-{now.year}-{now.month}-{now.day}-{now.hour:02d}-{now.minute:02d}.mp4"
        # 保险起见再做一次非法字符清理（Windows）
        illegal = r'[<>:"/\\|?*\x00-\x1F]'
        fname = re.sub(illegal, "_", fname)

        cand = base_dir / fname
        if cand.exists():
            i = 1
            while True:
                fname2 = f"final-{now.year}-{now.month}-{now.day}-{now.hour:02d}-{now.minute:02d}-{i:03d}.mp4"
                fname2 = re.sub(illegal, "_", fname2)
                cand2 = base_dir / fname2
                if not cand2.exists():
                    cand = cand2
                    break
                i += 1
        out_path = str(cand)
    # —— 小修改结束 —— #
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
