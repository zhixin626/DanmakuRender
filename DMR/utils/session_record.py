"""会话记录(Session Record)：把 liveevents 内存里的 state_dict / session_data 原样镜像到任务 output_dir 的 json。

一个 group 一份 json，文件名 .session_<group>.json，结构与内存 1:1 对应
（list 仍是 list，dict 仍是 dict，键名一致，层级一致）：
  {
    "group_id":     "<group>",
    "taskname":     "<task>",
    "session_data": { ... },             # 镜像 self.session_data[group_id]（is_live_end + stime/gifts/...）
    "state": [                           # 镜像 self.state_dict[group_id]（列表，每项一段）
       { "src_video_pre": {"status": ..., "file": <VideoInfo dict|null>, "wait": [...]},
         "src_video":     {"status": ..., "file": ...,                   "wait": [...]},
         "dm_video":      {"status": ..., "file": ...,                   "wait": [...]} },
       { ...第 2 段... }
    ],
    "updated_at":   "<iso>"
  }

唯一的转换：state 里的 file 是 VideoInfo 对象，落盘时序列化成 dict（datetime 用 __dt__ 包裹）。
本文件只负责"序列化 + 读写 + 删除"，不含业务逻辑，也没有任何重启恢复逻辑读它——
纯镜像，给人和 WebUI 看。json 与内存同生共死：reconcile 时写、组释放/程序退出时删。
"""
import os
import glob
import json
import logging
import tempfile
import threading
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

RECORD_PREFIX = '.session_'        # 文件名：.session_<group>.json，落在任务 output_dir


# ----------------------------------------------------------------------------
# datetime / tuple 的 JSON 编解码（file 字段里的 VideoInfo 含 datetime）
# ----------------------------------------------------------------------------
_DT_TAG = '__dt__'


def _encode(v):
    if isinstance(v, datetime):
        return {_DT_TAG: v.isoformat()}
    if isinstance(v, (tuple, list)):
        return [_encode(x) for x in v]
    if isinstance(v, dict):
        return {k: _encode(x) for k, x in v.items()}
    return v


def videoinfo_to_dict(vi):
    """VideoInfo(或任意 cpdict) -> 可 JSON 化的 dict；None 原样返回。"""
    if vi is None:
        return None
    return {k: _encode(v) for k, v in dict(vi).items()}


def humanize_dt(obj):
    """把读出来的 json 里 {__dt__: iso} 还原成 iso 字符串（便于直接展示），其余原样。"""
    if isinstance(obj, dict):
        if len(obj) == 1 and _DT_TAG in obj:
            return obj[_DT_TAG]
        return {k: humanize_dt(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [humanize_dt(x) for x in obj]
    return obj


# ----------------------------------------------------------------------------
# 原子读写
# ----------------------------------------------------------------------------
_io_lock = threading.Lock()


def _atomic_write_json(path, obj):
    """同目录写临时文件再 os.replace，避免读到半截 json。"""
    d = os.path.dirname(os.path.abspath(path)) or '.'
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix='.tmp_', suffix='.json')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def record_path(output_dir, group_id):
    safe = str(group_id).replace('/', '_').replace('\\', '_')
    return os.path.join(output_dir, f'{RECORD_PREFIX}{safe}.json')


# ----------------------------------------------------------------------------
# 写：把内存 state_dict[group_id] / session_data[group_id] 忠实镜像落盘
# ----------------------------------------------------------------------------
def _serialize_state(state_list):
    """忠实序列化 state_dict[group_id]（list[ {vtype:{status,file,wait}} ]）：
    list 还是 list，每段还是 dict，键名 status/file/wait 原样；只把 file(VideoInfo) 转成 dict。"""
    out = []
    for seg in (state_list or []):
        if not isinstance(seg, dict):
            continue
        seg_out = {}
        for vtype, slot in seg.items():
            if not isinstance(slot, dict):
                continue
            seg_out[vtype] = {
                'status': slot.get('status'),
                'file':   videoinfo_to_dict(slot.get('file')),
                'wait':   list(slot.get('wait') or []),
            }
        out.append(seg_out)
    return out


def write_record(output_dir, group_id, taskname, state_list, session_data=None):
    """把该 group 的内存状态忠实镜像成 json 落盘，返回写入路径。"""
    path = record_path(output_dir, group_id)
    obj = {
        'group_id':     str(group_id),
        'taskname':     taskname,
        'session_data': _encode(session_data or {}),
        'state':        _serialize_state(state_list),
        'updated_at':   datetime.now().isoformat(timespec='seconds'),
    }
    _atomic_write_json(path, obj)
    return path


# ----------------------------------------------------------------------------
# 读：供 WebUI 可视化（原子写保证永远读到完整 json）
# ----------------------------------------------------------------------------
def list_record_files(output_dir):
    """列出某任务 output_dir 下所有未删除的会话记录文件（按文件名序）。"""
    return sorted(glob.glob(os.path.join(output_dir, f'{RECORD_PREFIX}*.json')))


def read_record(path):
    """读一份会话记录 json；datetime 还原成 iso 字符串便于展示。损坏/缺失返回 None。"""
    try:
        with _io_lock:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        return humanize_dt(data)
    except Exception as e:
        logger.debug(f'读会话记录失败(已忽略): {path}: {e}')
        return None


def read_all_records(output_dir):
    """读某任务 output_dir 下全部未删除的会话记录，返回 list（顺序同文件名）。"""
    out = []
    for p in list_record_files(output_dir):
        rec = read_record(p)
        if rec is not None:
            out.append(rec)
    return out


# ----------------------------------------------------------------------------
# 删：组释放删单份；程序退出删整个任务的全部
# ----------------------------------------------------------------------------
def delete_record(output_dir, group_id):
    """删某 group 的会话记录（组处理完释放内存时调）。吞异常。"""
    p = record_path(output_dir, group_id)
    try:
        if os.path.exists(p):
            os.remove(p)
    except OSError as e:
        logger.debug(f'删除会话记录失败(已忽略): {p}: {e}')


def delete_all_records(output_dir):
    """删某任务 output_dir 下所有会话记录（程序退出、liveevents 死时调）。吞异常。"""
    for p in list_record_files(output_dir):
        try:
            os.remove(p)
        except OSError as e:
            logger.debug(f'删除会话记录失败(已忽略): {p}: {e}')


# ----------------------------------------------------------------------------
# 直播时间记录（旧格式 _live_sessions.txt）：读取最近一次开播/下播时间
# 与上面的 .session_<group>.json 不是同一份文件，这里只做时间读取，供文件名/时长模板使用。
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
