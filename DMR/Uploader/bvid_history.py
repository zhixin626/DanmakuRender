"""BVID 周期历史持久化（B站「追加到已有稿件」功能用）。

把每个周期(monthly/daily)、每个账号上传得到的 bvid 记到录制输出目录下的
bvid_history.json；下次同周期上传时读出来作为 base_bvid，从而追加到同一稿件。

纯文件读写、无 DMR 依赖（叶子模块），供 liveevents 编排时机调用、upload_only 手动上传复用。
bvid 是 B站特有概念，故归属 Uploader 包而非 Task。
"""
import json
from pathlib import Path
from datetime import datetime


def get_bvid_history_file(src_path):
    """获取统一的 BVID 历史文件路径"""
    return Path(src_path) / "bvid_history.json"


def _make_period_key(stime, period):
    """根据 period ('monthly'/'daily') 和 stime 生成 bvid_history.json 的 key。"""
    if isinstance(stime, datetime):
        dt = stime
    elif isinstance(stime, (int, float)):
        dt = datetime.fromtimestamp(stime)
    else:
        dt = datetime.now()

    if period == 'daily':
        return f"{dt.year}-{dt.month:02d}-{dt.day:02d}"
    else:  # monthly（默认）
        return f"{dt.year}-{dt.month:02d}"


def read_period_bvid(src_path, stime, period='monthly', account=None):
    """
    根据 stime 和 period 读取对应周期的 BVID。
    返回: str (BVID) 或 None
    """
    key = _make_period_key(stime, period)
    if account:
        key = f"{key}_{account}"
    file_path = get_bvid_history_file(src_path)

    if file_path.exists():
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                full_history = json.load(f)
                return full_history.get(key)
        except Exception as e:
            print(f"读取 BVID 历史文件失败: {e}")
    return None


def write_period_bvid(src_path, stime, bvid, period='monthly', account=None):
    """
    更新 BVID 到统一的 JSON 文件。
    monthly 格式: {"2026-03": "bvid1"}，daily 格式: {"2026-03-05": "bvid1"}
    """
    key = _make_period_key(stime, period)
    if account:
        key = f"{key}_{account}"
    file_path = get_bvid_history_file(src_path)

    full_history = {}
    if file_path.exists():
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                full_history = json.load(f)
        except Exception:
            full_history = {}

    full_history[key] = bvid

    try:
        if not file_path.parent.exists():
            file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(full_history, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"写入 BVID 历史文件失败: {e}")
