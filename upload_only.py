import yaml
from pathlib import Path
import logging
from datetime import datetime
from DMR.utils.merge_mp4 import *
from DMR.utils import *
from DMR.LiveAPI import LiveAPI
from DMR.utils.dataclass import VideoInfo, StreamerInfo
from DMR.Task.liveevents import LiveEvents, read_monthly_bvids, write_monthly_bvid

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
        raise RuntimeError("找不到config文件")

    logger.info(f"将按照 {yml_path} 的配置信息")
    with open(yml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return taskname, vtype, data


def file_to_args(file_path):
    """返回 (common_event_args, download_args, [upload_config, ...])。"""
    taskname, vtype, data = load_config(file_path)
    common_event_args = data.get("common_event_args")
    download_args = data.get("download_args")
    raw_upload_args = _pick_upload_arg(data.get("upload_args", {}), vtype)
    if isinstance(raw_upload_args, dict):
        raw_upload_args = [raw_upload_args]
    return common_event_args, download_args, raw_upload_args


# ── LiveEvents 实例化 ─────────────────────────────────────

def _make_live_events(file_path) -> tuple:
    """
    加载 YAML 并创建 LiveEvents 实例，手动初始化路径和 live_status，
    完全绕过 onLiveStart（从而避免触发 check_render_cover 和 bark_notify）。
    返回 (live_events, download_args)。
    """
    taskname, vtype, data = load_config(file_path)
    download_args = data.get("download_args", {})

    live_events = LiveEvents(taskname, data)
    live_events.src_path       = Path(str(download_args.get("output_dir")))
    live_events.dmvideo_path   = Path(str(download_args.get("output_dir")) + "（弹幕版）")
    live_events.transcode_path = Path(str(download_args.get("output_dir")) + "（转码后）")

    # 手动初始化 live_status，避免调用 onLiveStart
    live_events.live_status[_MANUAL_GROUP_ID] = {
        "is_already_render_cover": False,
        "is_live_end": True,
        "gift_stat": {
            "total_revenue": "未知",
            "total_gifters": "未知",
            "top_ranking":   "未知",
        },
    }
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

def build_videoinfo(file_path: str) -> VideoInfo:
    p = Path(file_path)
    if not p.exists():
        raise FileNotFoundError(file_path)

    _, download_args, _ = file_to_args(file_path)
    out_dir = download_args.get("output_dir")

    st, et = read_last_complete_session(out_dir, only_start=False)

    api = LiveAPI(download_args.get("url"))
    roominfo = api.GetRoomInfo()
    streamer = StreamerInfo(name=roominfo.get("name", ""), url=download_args.get("url"))

    raw_stat = get_last_gift_stats(out_dir)
    names_list = [f"{item['name']}({item['total_value']})" for item in raw_stat.get("top_ranking", [])]

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

    return VideoInfo(
        path          = str(p),
        dtype         = dtype,
        size          = st_stat.st_size,
        ctime         = datetime.fromtimestamp(st_stat.st_ctime),
        stime         = st,
        etime         = et,
        totaltime     = format_duration(st, et),
        duration      = media["duration"],
        resolution    = media["resolution"],
        title         = roominfo.get("title", ""),
        streamer      = streamer,
        taskname      = taskname,
        total_revenue = raw_stat.get("total_revenue", 0),
        total_gifters = raw_stat.get("total_gifters", 0),
        top_ranking   = ",".join(names_list),
    )


# ── 上传流程 ──────────────────────────────────────────────

def upload_process(video_info: VideoInfo, account_config: dict, engine_type: str,
                   live_events: LiveEvents):
    """
    单账号上传：
    1. 若配置了 auto_append_monthly，注入 base_bvid（对齐 LiveEvents._check_for_upload）
    2. 调用 DMR 的 uploader
    3. 上传成功后写入 bvid 历史，并通过 live_events.check_add_to_list 处理合集
    """
    account = account_config.get("account")
    cfg = account_config.copy()

    # --- auto_append_monthly：注入 base_bvid ---
    if cfg.get("auto_append_monthly"):
        st, _ = read_last_complete_session(live_events.src_path, only_start=False)
        target_bvid = read_monthly_bvids(live_events.src_path, st, account=account)
        if target_bvid:
            cfg["base_bvid"] = target_bvid
            logger.info(f"[账号{account}] 追加模式：检测到当月已有稿件 {target_bvid}，将追加至该稿件")
        else:
            cfg["base_bvid"] = ""
            logger.info(f"[账号{account}] 追加模式：当月暂无稿件，将创建新稿件")

    if engine_type == "biliuprs":
        from DMR.Uploader.biliuprs import biliuprs
        uploader = biliuprs(account=account)
    else:
        from DMR.Uploader.biliwebapi import BiliWebApi
        uploader = BiliWebApi(account=account, cookies=f".login_info/{account}.json")

    status, bvid = uploader.upload([video_info], **cfg)

    if status and bvid:
        logger.info(f"账号 {account} 上传成功, bvid: {bvid}")

        # --- 写 bvid 历史（对齐 LiveEvents.onUploadEnd） ---
        if cfg.get("auto_append_monthly") and bvid != cfg.get("base_bvid", ""):
            try:
                st, _ = read_last_complete_session(live_events.src_path, only_start=False)
                write_monthly_bvid(live_events.src_path, st, bvid, account=account)
                logger.info(f"已登记 {st.month} 月 账号 {account} bvid: {bvid}")
            except Exception as e:
                logger.error(f"登记 bvid 失败: {e}")

        # --- 加入合集（通过 LiveEvents.check_add_to_list，不重复造轮子） ---
        live_events.check_add_to_list(_MANUAL_GROUP_ID, bvid, cfg)
    else:
        logger.error(f"账号 {account} 上传失败: {bvid}")


# ── 封面渲染（直接调用 LiveEvents.check_render_cover，避免 bark_notify） ──

def re_render_cover(live_events: LiveEvents, download_args: dict):
    url = download_args.get("url", "")
    live_events.check_render_cover(_MANUAL_GROUP_ID, url=url)


# ── 工具函数 ──────────────────────────────────────────────

def strip_quotes(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and ((s[0] == s[-1] == '"') or (s[0] == s[-1] == "'")):
        return s[1:-1]
    return s


# ── 主流程 ────────────────────────────────────────────────

def main():
    video_path = strip_quotes(input("请输入需要上传的视频路径：\n").strip())

    # 加载配置 & 创建 LiveEvents 实例（不触发 onLiveStart）
    live_events, download_args = _make_live_events(video_path)
    _, _, all_upload_configs = file_to_args(video_path)
    video_info = build_videoinfo(video_path)

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

    engine = input("请选择上传引擎 [1=biliuprs, 2=biliWebAPI] (默认 1)：").strip()
    engine_type = "biliWebAPI" if engine in ("2", "biliwebapi") else "biliuprs"

    render_choice = input("是否重新渲染封面 [1=是, 2=否] (默认 2): ").strip()
    if render_choice == "1":
        logger.info("正在重新渲染封面...")
        re_render_cover(live_events, download_args)

    for cfg in selected_configs:
        logger.info(f"开始处理账号: {cfg.get('account')}")
        upload_process(video_info, cfg, engine_type, live_events)


if __name__ == "__main__":
    main()
