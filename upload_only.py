import yaml
from pathlib import Path
import logging
from datetime import datetime
from DMR.utils.video_merge import probe_media
from DMR.utils.session_record import read_last_complete_session
from DMR.utils import *
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


def strip_quotes(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and ((s[0] == s[-1] == '"') or (s[0] == s[-1] == "'")):
        return s[1:-1]
    return s


# ── 配置加载 ─────────────────────────────────────────────

def _pick_upload_arg(upload_args_dict: dict, vtype: str) -> dict:
    if vtype in upload_args_dict:
        return upload_args_dict[vtype]
    for k, cfg in upload_args_dict.items():
        parts = [p.strip() for p in str(k).split("+")]
        if vtype in parts:
            return cfg
    raise RuntimeError(f"upload_args 里找不到适用于 {vtype} 的配置（支持 dm_video+src_video 形式）")


def load_config(file_path):
    """从文件路径推断 taskname/vtype，加载完整 YAML，返回 (taskname, vtype, data)。"""
    taskname = Path(file_path).parent.name
    if taskname.endswith("（弹幕版）"):
        vtype = "dm_video"
        taskname = taskname[:-len("（弹幕版）")].strip()
    elif taskname.endswith("（转码后）"):
        vtype = "src_video"
        taskname = taskname[:-len("（转码后）")].strip()
    else:
        raise RuntimeError(f"未知视频类型：{file_path}")

    yml_path = None
    for prefix in ["./configs/DMR-", "./configs/test-"]:
        if Path(f"{prefix}{taskname}.yml").exists():
            yml_path = f"{prefix}{taskname}.yml"
            break
    if not yml_path:
        matched = list(Path("./configs").glob(f"*{taskname}*.yml"))
        if len(matched) == 1:
            yml_path = str(matched[0])
        elif len(matched) > 1:
            print(f"找到多个匹配的配置文件，请选择：")
            for i, p in enumerate(matched):
                print(f"[{i}] {p}")
            idx = input("请输入序号: ").strip()
            yml_path = str(matched[int(idx)])
        else:
            raise RuntimeError(f"找不到包含 '{taskname}' 的配置文件")

    logger.info(f"将按照 {yml_path} 的配置信息")
    with open(yml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return taskname, vtype, data


def file_to_args(file_path):
    """返回 (common_event_args, download_args, [upload_config, ...])。"""
    taskname, vtype, data = load_config(file_path)
    common_event_args = data.get("common_event_args") or {}
    # 兼容：merge_args/bark_args 现已上移为顶层；回填进 common_event_args，旧调用方无需改动
    for key in ("merge_args", "bark_args"):
        if data.get(key) is not None and key not in common_event_args:
            common_event_args[key] = data.get(key)
    download_args = data.get("download_args")
    raw_upload_args = _pick_upload_arg(data.get("upload_args", {}), vtype)
    if isinstance(raw_upload_args, dict):
        raw_upload_args = [raw_upload_args]
    return common_event_args, download_args, raw_upload_args


# ── LiveEvents 实例化 ─────────────────────────────────────

def _make_live_events(file_path) -> tuple:
    """
    加载 YAML 并创建 LiveEvents 实例，手动初始化路径和 session_data，
    完全绕过 onLiveStart（从而避免触发 bark_notify 等）。
    返回 (live_events, download_args)。
    """
    taskname, vtype, data = load_config(file_path)
    download_args = data.get("download_args", {})

    live_events = LiveEvents(taskname, data)
    # src_path/dmvideo_path/transcode_path 已在 LiveEvents.__init__ 里按 output_dir 算好，无需手动设置

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
    return live_events, download_args


# ── 礼物统计（LiveEvents 只从消息读，这里需要从磁盘读） ────────

def get_last_gift_stats(folder_path):
    file_path = os.path.join(folder_path, "gifts_statistics.jsonl")
    if not os.path.exists(file_path):
        return {}
    last_entry = None
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    last_entry = json.loads(line)
    except Exception as e:
        logger.warning(f"读取礼物统计出错: {e}")
        return {}
    return last_entry if last_entry else {}


# ── VideoInfo 构建 ────────────────────────────────────────

def build_videoinfo(file_path: str, live_events) -> VideoInfo:
    """
    构建基础 VideoInfo，会话级字段（礼物统计、时间）由 live_events._backfill_video_info 统一填入。
    live_events 须已调用过 _collect_session_data。
    """
    p = Path(file_path)
    if not p.exists():
        raise FileNotFoundError(file_path)

    _, download_args, _ = file_to_args(file_path)

    try:
        api      = LiveAPI(download_args.get("url"))
        roominfo = api.GetRoomInfo() or {}
    except Exception:
        roominfo = {}

    streamer = StreamerInfo(
        name = roominfo.get("name", ""),
        url  = download_args.get("url", ""),
    )

    st_stat = p.stat()
    media   = probe_media(str(p))

    taskname = p.parent.name
    if taskname.endswith("（弹幕版）"):
        dtype    = "dm_video"
        taskname = taskname[:-len("（弹幕版）")].strip()
    elif taskname.endswith("（转码后）"):
        dtype    = "src_video"
        taskname = taskname[:-len("（转码后）")].strip()
    else:
        dtype = ""

    video_info = VideoInfo(
        path       = str(p),
        dtype      = dtype,
        file_id    = uuid(),
        size       = st_stat.st_size,
        ctime      = datetime.fromtimestamp(st_stat.st_ctime),
        duration   = media["duration"],
        resolution = media["resolution"],
        title      = roominfo.get("title", ""),
        streamer   = streamer,
        taskname   = taskname,
        group_id   = _MANUAL_GROUP_ID,
        segment_id = 1,
    )

    # 把 VideoInfo 注入 state_dict，让 _backfill_video_info 能找到它
    if _MANUAL_GROUP_ID not in live_events.state_dict:
        live_events.state_dict[_MANUAL_GROUP_ID] = []
    live_events.state_dict[_MANUAL_GROUP_ID].append({
        dtype: {"status": "ready", "file": video_info, "wait": []}
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
    acfun 引擎支持传入多个 VideoInfo 实现分P上传，其他引擎仅使用第一个。
    """
    video_info = video_infos[0]
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

    upload_files = video_infos if engine_type == "acfun" else [video_info]
    status, result = uploader.upload(upload_files, **cfg)

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



# ── 主流程 ────────────────────────────────────────────────

def main():
    video_path = strip_quotes(input("请输入需要上传的视频路径：\n").strip())

    # 加载配置 & 创建 LiveEvents 实例（不触发 onLiveStart）
    live_events, download_args = _make_live_events(video_path)
    _, _, all_upload_configs = file_to_args(video_path)
    video_info = build_videoinfo(video_path, live_events)
    video_infos = [video_info]

    # 选择账号
    print("\n检测到以下上传账号配置：")
    for i, cfg in enumerate(all_upload_configs):
        print(f"[{i}] 账号: {cfg.get('account')} (标题模板: {str(cfg.get('title', ''))[:30]}...)")

    choice = input("\n请选择上传账号 [输入索引数字, 或 'all' 全部上传] (默认 all): ").strip().lower()
    if choice == "all" or choice == "":
        selected_configs = all_upload_configs
    else:
        try:
            selected_configs = [all_upload_configs[int(choice)]]
        except Exception:
            print("❌ 输入错误，取消上传")
            return

    # acfun 多P：若选中的配置中有 acfun 引擎，允许追加更多视频
    if any(cfg.get("engine") == "acfun" for cfg in selected_configs):
        print("\n检测到 AcFun 引擎，支持多P上传。")
        print("请继续输入更多视频路径作为后续分P（直接回车结束输入）：")
        while True:
            extra = strip_quotes(input(f"P{len(video_infos) + 1} 路径（回车结束）: ").strip())
            if not extra:
                break
            try:
                video_infos.append(build_videoinfo(extra, live_events))
                print(f"已添加 P{len(video_infos)}: {extra}")
            except Exception as e:
                print(f"❌ 读取失败，跳过: {e}")
        if len(video_infos) > 1:
            logger.info(f"共 {len(video_infos)} P 将一起上传")

    for cfg in selected_configs:
        logger.info(f"开始处理账号: {cfg.get('account')}")
        upload_process(video_infos, cfg, live_events)


if __name__ == "__main__":
    main()
