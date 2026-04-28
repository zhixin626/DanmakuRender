import subprocess as sp
import re,yaml
from pathlib import Path
import logging
from DMR.utils.merge_mp4 import *
from DMR.utils import *
from DMR.utils.utils import replace_keywords
import requests
from DMR.LiveAPI import LiveAPI
from DMR.utils.merge_mp4 import *
from DMR.Uploader.biliwebapi import BiliWebApi
from DMR.utils.dataclass import VideoInfo

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
# logger = logging.getLogger("DMR")
logger = logging.getLogger() # root
logger.addHandler(handler)
logger.setLevel(logging.INFO)

def run_biliuprs(
        file_path,
        account,
        cover,title,desc,dynamic,extra_args,tag,source,limit,
        no_reprint,is_only_self,copyright,dtime,tid,
        is_upload=False,
        show_progress_bar=True,
        show_cmd=True,
        **kwargs, # 用来捕获多余的参数，防止报错
    ):
    cmd=[
    'biliup',
    '-u',f'.login_info/{account}.json',
    'upload',
    '--copyright',copyright,
    '--cover',cover,
    '--desc',desc,
    '--title',title,
    '--dynamic',dynamic,
    '--dtime',dtime,
    '--limit',limit,
    '--no-reprint',no_reprint,
    '--is-only-self',is_only_self,
    '--source',source,
    '--tag',tag,
    '--tid',tid,
    file_path
]
    if extra_args:
        for arg in extra_args:
            cmd.append(arg)
    cmd = [str(x) for x in cmd]
    if show_cmd:
        logger.info(f"cmd is : {cmd}")
    if not is_upload:
        logger.info("不进行上传")
        return None
    if not show_progress_bar:
        logger.info("正在上传......")
        result = sp.run(cmd, capture_output=True, text=True, errors='ignore')
        stdout = result.stdout or ""
        stderr = result.stderr or ""
        match = re.search(r'BV[0-9A-Za-z]{10}', stdout)
        if not match:
            match = re.search(r'BV[0-9A-Za-z]{10}', stderr)
        if match:
            bvid = match.group(0)
            return bvid
        raise RuntimeError(stdout+"\n"+stderr)
    else:
        logger.info("正在上传......")
        sp.run(cmd)
        time.sleep(3)
        n=8
        for attempt in range(n):
            bvid = get_bvid(
                0,
                checker={"title": title, "dynamic": dynamic},
                account=account
                )
            if bvid:
                return bvid
            if attempt < n:
                time.sleep(5)
        return None

def _pick_upload_arg(upload_args_dict: dict, vtype: str) -> dict:
    if vtype in upload_args_dict:
        return upload_args_dict[vtype]
    for k, cfg in upload_args_dict.items():
        parts = [p.strip() for p in str(k).split("+")]
        if vtype in parts:
            return cfg
    raise RuntimeError(f"upload_args 里找不到适用于 {vtype} 的配置（支持 dm_video+src_video 形式）")

def file_to_args(file_path):
    taskname = Path(file_path).parent.name
    if taskname.endswith("（弹幕版）"):
        suffix = "danmaku"
        taskname = taskname[:-len("（弹幕版）")].strip()
    elif taskname.endswith("（转码后）"):
        suffix = "transcode"
        taskname = taskname[:-len("（转码后）")].strip()
    else:
        suffix = "none"
        taskname = taskname

    # 获取 yml 路径
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

    common_event_args = data.get("common_event_args")
    download_args = data.get("download_args")

    if "（弹幕版）" in file_path:
        vtype = "dm_video"
    elif "（转码后）" in file_path:
        vtype = "src_video"
    else:
        raise RuntimeError(f"未知视频类型：{file_path}")

    upload_args_dict = data.get("upload_args", {})
    # 这里获取到的可能是 list [账号1, 账号2]
    raw_upload_args = _pick_upload_arg(upload_args_dict, vtype)

    # 统一转为 list 方便后续处理
    if isinstance(raw_upload_args, dict):
        raw_upload_args = [raw_upload_args]

    return common_event_args, download_args, raw_upload_args

def get_last_gift_stats(folder_path):
    """
    读取文件夹下的 gifts_statistics.jsonl，返回最后一行统计数据的字典。
    """
    file_path = os.path.join(folder_path, "gifts_statistics.jsonl")

    if not os.path.exists(file_path):
        print(f"文件未找到: {file_path}")
        return {}

    last_entry = None
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            # 逐行读取以节省内存，保留最后一行
            for line in f:
                line = line.strip()
                if line:
                    last_entry = json.loads(line)
    except Exception as e:
        print(f"读取文件出错: {e}")
        return {}

    return last_entry if last_entry else {}

def replace_args_keywords(
    common_event_args,
    download_args,
    upload_args,
    ):
    api=LiveAPI(download_args.get("url"))
    roominfo=api.GetRoomInfo()
    TITLE=roominfo.get("title","")
    NAME=roominfo.get("name","")

    gift_stat_folder=live_time_path=download_args.get("output_dir")
    raw_stat = get_last_gift_stats(gift_stat_folder)
    names_list = [f"{item['name']}({item['total_value']})" for item in raw_stat.get("top_ranking", [])]
    names_str = ",".join(names_list)
    st,et=read_last_complete_session(live_time_path,only_start=False)
    kw_info = {
        "streamer": {
            "name": NAME,
            "url": download_args.get("url"),
        },
        "stime": {
            "year": st.year,
            "month": st.month,
            "day": st.day,
            "hour": st.hour,
            "minute": st.minute,
        },
        "etime": {
            "year": et.year,
            "month": et.month,
            "day": et.day,
            "hour": et.hour,
            "minute": et.minute,
        },
        "title": TITLE,
        "totaltime": format_duration(st, et),
        "total_revenue": raw_stat.get("total_revenue", 0),
        "total_gifters": raw_stat.get("total_gifters", 0),
        "top_ranking":names_str,
    }
    title=replace_keywords(upload_args.get("title"),kw_info)
    desc=replace_keywords(upload_args.get("desc"),kw_info)
    dynamic=replace_keywords(upload_args.get("dynamic"),kw_info)
    after_upload_args=common_event_args.get("after_upload_args",{})
    # insert_desc=replace_keywords(after_upload_args.get("insert_desc"),kw_info)
    upload_args["title"]   = title
    upload_args["desc"]    = desc
    upload_args["dynamic"] = dynamic
    # common_event_args["after_upload_args"]["insert_desc"]=insert_desc

    return common_event_args,download_args,upload_args

def _re_render_cover(file_path):
    common_event_args,download_args,_=replace_args_keywords(*file_to_args(file_path))
    name=common_event_args.get("cover_args",{}).get("name")
    output_dir=common_event_args.get("cover_args",{}).get("output_dir")
    name_color=common_event_args.get("cover_args",{}).get("name_color")

    stime,_=read_last_complete_session(download_args.get("output_dir"))
    time=f"{stime.month}月{stime.day}日"
    year=f"{stime.year}"
    from DMR.utils.render_with_manimgl import rendercover_with_manimgl
    rendercover_with_manimgl(name, time,name_color,year,output_dir)

def _re_add_to_list(file_path, bvid, account_config):
    """
    修改后的合集逻辑：直接从传入的账号配置字典中读取
    """
    need_add_to_list = account_config.get("add_to_list", False)
    account = account_config.get("account")
    sectionId = account_config.get("sectionId")

    if need_add_to_list and sectionId:
        logger.info(f"正在将 {bvid} 加入账号 {account} 的合集 {sectionId}")
        add_to_list(bvid, sectionId, account)
    else:
        logger.info(f"账号 {account} 未配置合集或 add_to_list 为 False，跳过。")

def upload_process(file_path, account_config, engine_type, show_progress_bar, show_cmd):
    """
    封装单账号上传流程
    """
    if engine_type == "biliuprs":
        bvid = run_biliuprs(
            file_path,
            is_upload=True,
            show_progress_bar=show_progress_bar,
            show_cmd=show_cmd,
            **account_config
        )
    else: # biliWebAPI
        account = account_config.get("account")
        cookies = f"D:/DanmakuRender/.login_info/{account}.json"
        api = BiliWebApi(cookies=cookies, account=account)
        _, bvid = api.upload([build_videoinfo(file_path)], **account_config)

    if bvid:
        logger.info(f"账号 {account_config.get('account')} 上传成功, bvid: {bvid}")
        _re_add_to_list(file_path, bvid, account_config)
    else:
        logger.error(f"账号 {account_config.get('account')} 获取 bvid 失败")

def get_bvid(n:int,checker=None,account=3546637425182939):
    url="https://member.bilibili.com/x/web/archives"
    headers=build_headers()
    cookies=get_cookies(account)
    params={
        "status":"is_pubing,pubed,not_pubed",
        "pn":"1",
        "ps":"10",
        "coop":"1",
        "interactive":"1"
    }
    r=requests.get(url,headers=headers,cookies=cookies,params=params)
    j=r.json()
    if j.get("code") != 0:
        raise RuntimeError("获取bvid失败")
    else:
        videos=j.get("data").get("arc_audits")
        video=videos[n].get("Archive")
        bvid=video.get("bvid")
        if not checker:
            return bvid

        for key, value in checker.items():
            real = video.get(key)
            if real != value:
                logger.info(f"字段 '{key}' 不匹配：实际值 = {real!r}，期望值 = {value!r}")
                return None
        return bvid

def strip_quotes(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and ((s[0] == s[-1] == '"') or (s[0] == s[-1] == "'")):
        return s[1:-1]
    return s

def build_videoinfo(file_path: str) -> VideoInfo:
    p = Path(file_path)
    if not p.exists():
        raise FileNotFoundError(file_path)

    # 你现有的：读 yml + 根据直播间信息替换 title/desc/dynamic 等
    common_event_args, download_args, upload_args = replace_args_keywords(*file_to_args(file_path))

    # 你现有的：读开播/下播时间（依赖 output_dir 的 _livestart_times.txt 等）
    out_dir = download_args.get("output_dir")
    st,et=read_last_complete_session(out_dir,only_start=False)

    # 你现有的：直播间信息
    api = LiveAPI(download_args.get("url"))
    roominfo = api.GetRoomInfo()
    NAME = roominfo.get("name", "")
    streamer = StreamerInfo(name=NAME, url=download_args.get("url"))

    # 文件系统信息
    st_stat = p.stat()
    size = st_stat.st_size
    ctime = datetime.fromtimestamp(st_stat.st_ctime)

    # ffprobe 补齐媒体信息
    media = probe_media(str(p))
    duration = media["duration"]
    resolution = media["resolution"]

    # taskname / dtype（你 file_to_args 里已经有类似逻辑，我这里复用一遍）
    taskname = p.parent.name
    if taskname.endswith("（弹幕版）"):
        dtype = "dm_video"
        taskname_clean = taskname[:-len("（弹幕版）")].strip()
    elif taskname.endswith("（转码后）"):
        dtype = "src_video"
        taskname_clean = taskname[:-len("（转码后）")].strip()
    else:
        dtype = ""
        taskname_clean = taskname

    title = upload_args.get("title")

    return VideoInfo(
        path=str(p),
        dtype=dtype,
        size=size,
        ctime=ctime,
        stime=st,
        etime=et,
        duration=duration,
        resolution=resolution,
        title=title,
        streamer=streamer,
        taskname=taskname_clean,
    )



def main():
    video_path = strip_quotes(input("请输入需要上传的视频路径：\n").strip())

    # 1. 解析配置
    common_args, dl_args, all_upload_configs = file_to_args(video_path)
    # 预处理关键字替换
    # 注意：这里需要对 list 里的每个 config 执行替换
    for i in range(len(all_upload_configs)):
        _, _, replaced_cfg = replace_args_keywords(common_args, dl_args, all_upload_configs[i])
        all_upload_configs[i] = replaced_cfg

    # 2. 选择账号
    print("\n检测到以下上传账号配置：")
    for i, cfg in enumerate(all_upload_configs):
        print(f"[{i}] 账号: {cfg.get('account')} (标题: {cfg.get('title')[:20]}...)")

    choice = input("\n请选择上传账号 [输入索引数字, 或输入 'all' 全部上传] (默认 all): ").strip().lower()

    selected_configs = []
    if choice == "all" or choice == "":
        selected_configs = all_upload_configs
    else:
        try:
            selected_configs = [all_upload_configs[int(choice)]]
        except:
            print("❌ 输入错误，取消上传")
            return

    # 3. 选择引擎及其他
    engine = input("请选择上传引擎 [1=biliuprs, 2=biliWebAPI] (默认 1)：").strip()
    engine_type = "biliWebAPI" if engine in ("2", "biliwebapi") else "biliuprs"

    render_choice = input("是否重新渲染封面 [1=是, 2=否] (默认 2): ").strip()
    re_render_cover = True if render_choice == "1" else False

    # 4. 执行封面渲染（针对视频文件执行一次即可）
    if re_render_cover:
        logger.info("正在重新渲染封面...")
        _re_render_cover(video_path)

    # 5. 执行上传
    # 注意：这里不再询问合集，直接将 re_add_to_list 设为 True，
    # 具体的 account_config 内部会根据自身的 add_to_list (True/False) 来决定动作
    for cfg in selected_configs:
        logger.info(f"▶️ 开始处理账号: {cfg.get('account')}")
        upload_process(
            video_path,
            cfg,
            engine_type,
            show_progress_bar=True,
            show_cmd=True,
        )

if __name__ == "__main__":
    main()




