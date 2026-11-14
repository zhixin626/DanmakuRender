"""手动上传：把已有视频按任务的上传配置投稿（多个视频即分P）。

用法：python upload_only.py
依次询问：视频（文件或文件夹）→ 任务配置 → 上传账号 → 确认。
"""

from pathlib import Path
import logging
import os
from datetime import datetime
from DMR.utils.ffprobe import probe_media
from DMR.utils.session_record import read_last_complete_session
from DMR.Config import Config
from DMR.utils import merge_dict, uuid
from DMR.utils.prompts import (
    OUTPUT_SUFFIXES,
    ask_choice,
    ask_numbers,
    ask_path,
    ask_task,
    confirm,
    read_task_file,
)
from DMR.LiveAPI import LiveAPI
from DMR.utils.dataclass import VideoInfo, StreamerInfo
from DMR.Task.liveevents import LiveEvents
from DMR.Uploader.bvid_history import read_period_bvid, write_period_bvid

import colorlog
handler = colorlog.StreamHandler()
handler.setFormatter(
    colorlog.ColoredFormatter(
        "%(asctime)s %(log_color)s%(levelname)s%(reset)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        log_colors={
            "DEBUG": "cyan",
            "INFO": "green",
            "WARNING": "yellow",
            "ERROR": "red",
            "CRITICAL": "red,bg_white",
        },
    )
)
logger = logging.getLogger()
logger.addHandler(handler)
logger.setLevel(logging.INFO)

# upload_only 专用的虚拟 group_id（只为满足 LiveEvents 内部状态检查）
_MANUAL_GROUP_ID = "upload_only"


VIDEO_TYPES = {"dm_video": "弹幕版", "src_video": "原画/转码"}


def upload_configs(config: Config, data: dict, vtype: str) -> list:
    """任务里适用于 vtype 的上传配置（支持 dm_video+src_video 形式的键），
    与主流程一样叠加在上传目标的全局默认之上。"""
    targets = config.global_config["upload_args"]
    for key, value in (data.get("upload_args") or {}).items():
        if vtype in (part.strip() for part in str(key).split("+")):
            return [
                merge_dict(targets.get(cfg.get("target", "bilibili")) or {}, cfg)
                for cfg in (value if isinstance(value, list) else [value])
            ]
    return []


# ── LiveEvents 实例化 ─────────────────────────────────────

def make_live_events(taskname: str, data: dict) -> LiveEvents:
    """
    创建 LiveEvents 实例并手动初始化 session_data，
    完全绕过 onLiveStart（从而避免触发 bark_notify 等）。
    """
    live_events = LiveEvents(taskname, data)
    # 手动初始化 session_data（结构与 onLiveStart 保持一致）
    live_events.session_data[_MANUAL_GROUP_ID] = {
        "rendered_cover_names": set(),
        "is_live_end"   : True,
        "gifts_revenue" : "",
        "num_of_gifters": "",
        "sc_revenue"    : "",
        "num_of_sc"     : "",
        "total_revenue" : "",
        "top_ranking"   : "",
        "stime"         : None,
        "etime"         : None,
        "totaltime"     : "",
    }
    # 从磁盘读取礼物统计和开播时间，写入 session_data
    live_events._collect_session_data(_MANUAL_GROUP_ID)
    return live_events


# ── VideoInfo 构建 ────────────────────────────────────────

def build_videoinfo(path: str, vtype: str, streamer: StreamerInfo, title: str,
                    live_events: LiveEvents) -> VideoInfo:
    """
    构建基础 VideoInfo，会话级字段（礼物统计、时间）由 live_events._backfill_video_info 统一填入。
    """
    p = Path(path)
    st_stat = p.stat()
    media   = probe_media(str(p))
    video_info = VideoInfo(
        path       = str(p),
        dtype      = vtype,
        file_id    = uuid(),
        size       = st_stat.st_size,
        ctime      = datetime.fromtimestamp(st_stat.st_ctime),
        duration   = media["duration"],
        resolution = media["resolution"],
        title      = title,
        streamer   = streamer,
        taskname   = live_events.name,
        group_id   = _MANUAL_GROUP_ID,
        segment_id = 1,
    )

    # 把 VideoInfo 注入 state_dict，让 _backfill_video_info 能找到它
    live_events.state_dict.setdefault(_MANUAL_GROUP_ID, []).append({
        vtype: {"status": "ready", "file": video_info, "wait": []}
    })
    # 统一回填会话级字段（礼物统计、stime/etime/totaltime 等）
    live_events._backfill_video_info(_MANUAL_GROUP_ID)
    return video_info


# ── 上传流程 ──────────────────────────────────────────────

def upload_process(video_infos: list, account_config: dict, live_events: LiveEvents):
    """
    单账号上传：
    1. 若配置了 auto_append_period，注入 base_bvid（对齐 LiveEvents._check_for_upload）
    2. 调用 DMR 的 uploader
    3. 上传成功后写入 bvid 历史；合集/封面由 uploader 按 cfg 自行处理
    多个 VideoInfo 按顺序作为分P，与主流程的整场上传一致。
    """
    account = account_config["account"]
    cfg = account_config.copy()

    # --- auto_append_period：注入 base_bvid ---
    period = cfg.get("auto_append_period")
    if period in ('monthly', 'daily'):
        st, _ = read_last_complete_session(live_events.src_path, only_start=False)
        target_bvid = read_period_bvid(live_events.src_path, st, period=period, account=account)
        if target_bvid:
            cfg["base_bvid"] = target_bvid
            logger.info(f"[账号{account}] 追加模式({period})：检测到已有稿件 {target_bvid}，将追加至该稿件")
        else:
            cfg["base_bvid"] = ""
            logger.info(f"[账号{account}] 追加模式({period})：暂无稿件，将创建新稿件")

    engine_type = cfg.get("engine", "biliuprs")
    base_bvid = cfg.get("base_bvid", "")
    if engine_type == "biliuprs":
        from DMR.Uploader.biliuprs import biliuprs
        uploader = biliuprs(account=account, base_bvid=base_bvid)
    elif engine_type == "biliwebapi":
        from DMR.Uploader.biliwebapi import BiliWebApi
        uploader = BiliWebApi(account=account, cookies=f".login_info/{account}.json", base_bvid=base_bvid)
    elif engine_type == "acfun":
        from DMR.Uploader.acfun import acfun
        uploader = acfun(account=account, show_progress=True)
    else:
        raise ValueError(f"未知上传引擎: {engine_type}")

    status, result = uploader.upload(video_infos, **cfg)

    if status:
        logger.info(f"账号 {account} 上传成功: {result}")

        if engine_type in ("biliuprs", "biliwebapi"):
            bvid = result
            # --- 写 bvid 历史（对齐 LiveEvents.onUploadEnd） ---
            _period = cfg.get("auto_append_period")
            if _period in ('monthly', 'daily') and bvid != cfg.get("base_bvid", ""):
                try:
                    st, _ = read_last_complete_session(live_events.src_path, only_start=False)
                    write_period_bvid(live_events.src_path, st, bvid, period=_period, account=account)
                    period_label = f"{st.year}-{st.month:02d}-{st.day:02d}" if _period == 'daily' else f"{st.year}-{st.month:02d}"
                    logger.info(f"已登记 {period_label} 账号 {account} bvid: {bvid}")
                except Exception as e:
                    logger.error(f"登记 bvid 失败: {e}")
            # 加入合集 / 封面：均由 uploader.upload(**cfg) 内部按 cfg 自行处理
    else:
        logger.error(f"账号 {account} 上传失败: {result}")



# ── 交互 ──────────────────────────────────────────────────

def is_video(path):
    return os.path.isfile(path) and path.lower().endswith((".mp4", ".flv"))


def ask_videos():
    path = ask_path(
        "请输入要上传的视频文件或文件夹",
        lambda p: os.path.isdir(p) or is_video(p),
        "路径不存在或不是 MP4/FLV/文件夹，请重新输入。",
    )
    if is_video(path):
        return [path]
    videos = sorted(
        (str(p) for p in Path(path).iterdir() if is_video(str(p))),
        key=os.path.getctime,
    )
    if not videos:
        print("该文件夹中没有 MP4/FLV。")
        return []
    print("该文件夹中的视频（按创建时间排序）：")
    for index, video in enumerate(videos, 1):
        print(f"  [{index}] {os.path.basename(video)}")
    picked = ask_numbers(
        "要上传的编号（多个即分P，按分P顺序空格分隔，n取消）：", len(videos), ()
    )
    return [videos[index] for index in picked]


def ask_video_type(folder):
    for suffix, vtype in OUTPUT_SUFFIXES.items():
        if folder.endswith(suffix):
            return vtype
    types = list(VIDEO_TYPES)
    return types[ask_choice("视频类型：", list(VIDEO_TYPES.values()))]


def ask_upload_task(config, folder):
    while True:
        taskname, filename = ask_task(config, folder)
        if taskname:
            return taskname, read_task_file(filename)
        print("上传需要具体任务的 upload_args，请选择任务。")


def ask_accounts(upload_configs):
    print("上传账号：")
    for index, cfg in enumerate(upload_configs, 1):
        print(
            f"  [{index}] {cfg.get('account')}（{cfg.get('engine', 'biliuprs')}）"
            f"  标题：{str(cfg.get('title', ''))[:30]}"
        )
    picked = ask_numbers(
        "账号编号（空格分隔，回车选全部，n取消）：",
        len(upload_configs),
        range(len(upload_configs)),
    )
    return [upload_configs[index] for index in picked]


def main():
    videos = ask_videos()
    if not videos:
        print("没有开始上传。")
        return
    folder = os.path.dirname(videos[0])
    vtype = ask_video_type(folder)
    config = Config("configs/global.yml")
    taskname, data = ask_upload_task(config, folder)
    accounts = upload_configs(config, data, vtype)
    if not accounts:
        print(f"任务「{taskname}」没有{VIDEO_TYPES[vtype]}的上传配置，没有开始上传。")
        return
    selected = ask_accounts(accounts)
    if not selected:
        print("没有开始上传。")
        return

    lines = [f"任务：{taskname}（{VIDEO_TYPES[vtype]}）"]
    lines += [f"P{index} {video}" for index, video in enumerate(videos, 1)]
    lines += [
        f"账号：{cfg.get('account')}（{cfg.get('engine', 'biliuprs')}）"
        for cfg in selected
    ]
    if not confirm(lines):
        print("没有开始上传。")
        return

    download_args = data.get("download_args") or {}
    try:
        roominfo = LiveAPI(download_args.get("url")).GetRoomInfo() or {}
    except Exception:
        roominfo = {}
    streamer = StreamerInfo(
        name=roominfo.get("name", ""), url=download_args.get("url", "")
    )
    live_events = make_live_events(taskname, data)
    video_infos = [
        build_videoinfo(video, vtype, streamer, roominfo.get("title", ""), live_events)
        for video in videos
    ]
    for cfg in selected:
        logger.info(f"开始处理账号: {cfg.get('account')}")
        upload_process(video_infos, cfg, live_events)


if __name__ == "__main__":
    main()
