import subprocess as sp
import re,yaml
from pathlib import Path
import logging
from DMR.utils.merge_mp4 import *
from DMR.utils import *
from DMR.utils.utils import replace_keywords
import requests
from DMR.LiveAPI import LiveAPI
from DMR.utils.merge_mp4 import probe_media
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
logger = logging.getLogger(__name__)
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

    if Path(f"./configs/DMR-{taskname}.yml").exists():
        yml_path=f"./configs/DMR-{taskname}.yml"
    elif Path(f"./configs/test-{taskname}.yml").exists():
        yml_path=f"./configs/test-{taskname}.yml"
    else:
        raise RuntimeError("找不到config文件")

    logger.info(f"将按照 {yml_path} 的配置信息")
    with open(yml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    common_event_args=data.get("common_event_args")
    download_args=data.get("download_args")
    if suffix == "danmaku":
        vtype = "dm_video"
    elif suffix == "transcode":
        vtype = "src_video"
    else:
        raise RuntimeError(f"未知视频类型，无法决定 upload_args：{taskname}")

    upload_args_dict = data.get("upload_args", {})
    upload_args = _pick_upload_arg(upload_args_dict, vtype)

    return common_event_args, download_args, upload_args

def replace_args_keywords(
    common_event_args,
    download_args,
    upload_args,
    ):
    api=LiveAPI(download_args.get("url"))
    roominfo=api.GetRoomInfo()
    TITLE=roominfo.get("title","")
    NAME=roominfo.get("name","")

    live_time_path=download_args.get("output_dir")
    st=read_live_time_from_path(live_time_path)
    et=read_live_time_from_path(live_time_path,is_start=False)
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
    }
    title=replace_keywords(upload_args.get("title"),kw_info)
    desc=replace_keywords(upload_args.get("desc"),kw_info)
    dynamic=replace_keywords(upload_args.get("dynamic"),kw_info)
    after_upload_args=common_event_args.get("after_upload_args",{})
    insert_desc=replace_keywords(after_upload_args.get("insert_desc"),kw_info)
    upload_args["title"]   = title
    upload_args["desc"]    = desc
    upload_args["dynamic"] = dynamic
    common_event_args["after_upload_args"]["insert_desc"]=insert_desc

    return common_event_args,download_args,upload_args

def _re_render_cover(file_path):
    common_event_args,download_args,_=replace_args_keywords(*file_to_args(file_path))
    name=common_event_args.get("cover_args",{}).get("name")
    output_dir=common_event_args.get("cover_args",{}).get("output_dir")
    name_color=common_event_args.get("cover_args",{}).get("name_color")
    stime=read_live_time_from_path(download_args.get("output_dir"))
    time=f"{stime.month}月{stime.day}日"
    year=f"{stime.year}"
    from DMR.utils.render_with_manimgl import rendercover_with_manimgl
    rendercover_with_manimgl(name, time,name_color,year,output_dir)

def _re_change_desc(file_path,bvid:str):
    common_event_args,_,_=replace_args_keywords(*file_to_args(file_path))
    after_upload_args  = common_event_args.get("after_upload_args")
    account            = after_upload_args.get("account")
    insert_desc= after_upload_args.get("insert_desc")
    add_livetime(bvid,insert_desc,account)

def _re_add_to_list(file_path,bvid:str):
    common_event_args,_,_=replace_args_keywords(*file_to_args(file_path))
    after_upload_args  = common_event_args.get("after_upload_args")
    account            = after_upload_args.get("account")
    sectionId          = after_upload_args.get("sectionId")
    add_to_list(bvid,sectionId,account)

def upload_video(
    file_path,
    show_progress_bar=False,
    show_cmd=False,
    re_change_desc=True,
    re_add_to_list=True,
    re_render_cover=False,
    ):
    _,_,upload_args=replace_args_keywords(*file_to_args(file_path))
    if re_render_cover:
        _re_render_cover(file_path)
    bvid=run_biliuprs(
        file_path,
        is_upload=True,
        show_progress_bar=show_progress_bar,
        show_cmd=show_cmd,
        **upload_args)
    if bvid :
        logger.info(f"bvid is {bvid}")
        if re_change_desc:
            _re_change_desc(file_path,bvid)
        if re_add_to_list:
            _re_add_to_list(file_path,bvid)
    else:
        logger.info("获取bvid失败")

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

def parse_yn(prompt: str, default: bool) -> bool:
    s = input(prompt).strip().lower()
    if s == "":
        logger.info(f"无输入，按默认值 {default} 处理。")
        return default
    if s in ("y", "yes", "1", "true", "t"):
        return True
    if s in ("n", "no", "0", "false", "f"):
        return False
    logger.info(f"输入无效，按默认值 {default} 处理。")
    return default

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
    st = read_live_time_from_path(out_dir)
    et = read_live_time_from_path(out_dir, is_start=False)

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

def upload_bybiliupWebAPI(
    file_path,
    # show_progress_bar=False,
    # show_cmd=False,
    re_change_desc=True,
    re_add_to_list=True,
    re_render_cover=False,
    ):
    _,_,upload_args=replace_args_keywords(*file_to_args(file_path))

    account=upload_args.get("account")
    cookies=f"D:/DanmakuRender/.login_info/{account}.json"
    api=BiliWebApi(cookies=cookies,account=account)

    if re_render_cover:
        _re_render_cover(file_path)
    _, bvid=api.upload([build_videoinfo(file_path)],**upload_args)

    if bvid :
        logger.info(f"bvid is {bvid}")
        if re_change_desc:
            _re_change_desc(file_path,bvid)
        if re_add_to_list:
            _re_add_to_list(file_path,bvid)
    else:
        logger.info("获取bvid失败")

def main():
    video_path = strip_quotes(input("请输入需要上传的视频路径：\n").strip())

    engine = input("请选择上传引擎 [1=biliuprs, 2=biliWebAPI] (默认 1)：").strip()
    if engine in ("", "1", "biliuprs"):
        engine = "biliuprs"
    elif engine in ("2", "biliwebapi"):
        engine = "biliWebAPI"
    else:
        print("❌ 无效输入，使用默认 biliuprs")
        engine = "biliuprs"

    re_render_cover = parse_yn("是否需要重新渲染封面[y/n], 回车默认为n：\n", default=False)
    re_change_desc = parse_yn("是否需要添加下播时间[y/n], 回车默认为y：\n", default=True)
    re_add_to_list = parse_yn("是否需要加入到合集[y/n], 回车默认为y：\n", default=True)

    if engine =="biliuprs":
        show_cmd = parse_yn("是否需要展示cmd命令[y/n], 回车默认为y：\n", default=True)
        show_progress = parse_yn("是否需要展示进度条[y/n], 回车默认为y：\n", default=True)

        upload_video(
            video_path,
            show_progress_bar=show_progress,
            show_cmd=show_cmd,
            re_change_desc=re_change_desc,
            re_add_to_list=re_add_to_list,
            re_render_cover=re_render_cover,
        )
    elif engine =="biliWebAPI":
        upload_bybiliupWebAPI(
            video_path,
            re_change_desc=re_change_desc,
            re_add_to_list=re_add_to_list,
            re_render_cover=re_render_cover
            )

if __name__ == "__main__":
    main()
    # file="D:/DanmakuRender/Tasks文件/不可一世杀手（弹幕版）/手12月15日23点02分（弹幕版）_merged_amplified.mp4"
    # _re_change_desc(file,bvid="BV1Yeq8BwE7S")


