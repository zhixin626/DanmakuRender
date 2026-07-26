"""会话状态的 JSON 序列化辅助 + 直播时间记录（_live_sessions.txt）读取。

原先这里还负责把 liveevents 内存镜像成 .session_<group>.json 落盘（纯镜像，给 WebUI 看）。
该机制已移除：WebUI 的 pipeline 视图(/api/tasks/<t>/pipeline)直接读内存现场序列化，
不再有磁盘状态文件，也就不存在重启残留、清理后无人回写等一致性问题。
留下的只有两块：
- encode_dt / humanize_dt / videoinfo_to_dict：datetime/tuple 的 JSON 编解码，供 webapi 序列化内存状态；
- read_last_complete_session：读 _live_sessions.txt 的开播/下播时间（独立的旧格式，与上述机制无关）。
"""
import logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------------
# datetime / tuple 的 JSON 编解码（VideoInfo / session_data 里含 datetime）
# ----------------------------------------------------------------------------
_DT_TAG = '__dt__'


def encode_dt(v):
    """把含 datetime/tuple 的嵌套结构转成可 JSON 化的形式（datetime 用 __dt__ 包裹）。"""
    if isinstance(v, datetime):
        return {_DT_TAG: v.isoformat()}
    if isinstance(v, (tuple, list)):
        return [encode_dt(x) for x in v]
    if isinstance(v, dict):
        return {k: encode_dt(x) for k, x in v.items()}
    return v


def videoinfo_to_dict(vi):
    """VideoInfo(或任意 cpdict) -> 可 JSON 化的 dict；None 原样返回。"""
    if vi is None:
        return None
    return {k: encode_dt(v) for k, v in dict(vi).items()}


def humanize_dt(obj):
    """把 {__dt__: iso} 还原成 iso 字符串（便于直接展示），其余原样。"""
    if isinstance(obj, dict):
        if len(obj) == 1 and _DT_TAG in obj:
            return obj[_DT_TAG]
        return {k: humanize_dt(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [humanize_dt(x) for x in obj]
    return obj


# ----------------------------------------------------------------------------
# 直播时间记录（旧格式 _live_sessions.txt）：读取最近一次开播/下播时间
# 供文件名/时长模板使用。
# ----------------------------------------------------------------------------
def read_last_complete_session(path, only_start=False):
    """
    读取直播时间记录。
    :param path: _live_sessions.txt 所在的目录路径
    :param only_start: 如果为 True，只寻找最近的一次“开播”时间，下播时间返回当前时间。
                       如果为 False，寻找最近的一条包含“开播”和“下播”的完整记录。
    :return: (start_time, end_time) 的 datetime 元组
    """
    txt_path = Path(path) / "_live_sessions.txt"
    default_now = datetime.now()

    try:
        if not txt_path.exists():
            return default_now, default_now

        with open(txt_path, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]

        # 从最后一行开始向上查找 (Search from bottom to top)
        for line in reversed(lines):
            parts = line.split(";")
            start_dt = None
            end_dt = None

            # 解析当前行中的所有部分
            for part in parts:
                part = part.strip()
                if part.startswith("开播:"):
                    start_part = part.replace("开播:", "").strip()
                    start_dt = datetime.fromisoformat(start_part)
                elif part.startswith("下播:"):
                    end_part = part.replace("下播:", "").strip()
                    end_dt = datetime.fromisoformat(end_part)

            # --- 核心逻辑 (Core Logic) ---
            if only_start:
                # 模式 A：只要找到了开播时间，就立即返回，结束时间设为当前
                if start_dt:
                    return start_dt, default_now
            else:
                # 模式 B：必须同时具备开播和下播
                if start_dt and end_dt:
                    return start_dt, end_dt

        # 如果遍历完所有行都没找到符合条件的
        return default_now, default_now

    except Exception as e:
        logger.warning(f"读取时间出错: {e}")
        return default_now, default_now
