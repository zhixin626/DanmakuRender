import subprocess as sp
import os
from datetime import datetime
from pathlib import Path
from pprint import pprint
import re
from send2trash import send2trash
import requests,yaml
import json
import logging
import requests
from DMR.Task.merge_mp4 import *
from DMR.utils import *
from DMR.utils.utils import replace_keywords
from DMR.utils.render_with_manimgl import rendercover_with_manimgl

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)
ACCOUNT=3546637425182939

def parse_title(title: str):
    # 日期正则
    date_pattern = re.compile(r"(\d{1,2})月(\d{1,2})日")

    # 书名号里面的字段
    bracket_pattern = re.compile(r"【(.*?)】")

    # 映射表（短称 → 全名）
    name_map = {
        "佐佐": "佐佐酱",
        "手儿": "不可一世杀手",
        "冷狗": "少年不太冷",
        "月亮3": "月亮3",
    }
    color_map = {
        "佐佐": "#DC75CD",
        "手儿": "#83C167",
        "冷狗": "#58C4DD",
        "月亮3": "#FFFF00",
    }
    # ① 提取日期
    date_match = date_pattern.search(title)
    if date_match:
        month, day = date_match.groups()
        date_str = f"{int(month)}月{int(day)}日"
    else:
        date_str = None

    # ② 提取书名号内容
    bracket_match = bracket_pattern.search(title)
    full_name = None
    color = "#000000"

    if bracket_match:
        inside = bracket_match.group(1)
        # 遍历短称，看看是否在里面
        for short, full in name_map.items():
            if short in inside:
                full_name = full
                color = color_map.get(short, "#000000")
                break

    return full_name,date_str, color

def get_section_info(section_id, account):
    """
    从合集(section)拿到每个分P的信息：
    返回 list[dict]，每个元素包含：
    bvid, title, name, date_str, color
    """
    url_section = "https://member.bilibili.com/x2/creative/web/season/section"
    cookies = get_cookies(account)
    headers = build_headers()
    section_id=parse_sectionId(section_id)

    r = requests.get(
        url_section,
        headers=headers,
        cookies=cookies,
        params={'id': section_id}
    )
    r.raise_for_status()
    j = r.json()

    data = j.get('data') or {}
    episodes = data.get('episodes') or []

    info_list = []
    for episode in episodes:
        title = episode.get("archiveTitle") or ""
        name, date_str, color = parse_title(title)
        bvid = episode.get("bvid")
        info_list.append({
            "bvid": bvid,
            "title": title,
            "name": name,
            "date_str": date_str,
            "color": color,
        })

    return info_list

def render_section_covers(
    section_id,
    account,
    **kwargs
):
    """
    根据 get_section_info 返回的 info_list 来批量渲染封面。
    只负责调 manimgl + 重命名，不再管 B 站接口。
    """
    info_list=get_section_info(section_id, account)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    for item in info_list:
        name = item["name"]
        date_str = item["date_str"]
        color = item["color"]

        # parse_title 失败时可能得到 None，这里简单跳过
        if not (name and date_str and color):
            continue
        else:
            render_covers(name,date_str,color,**kwargs)

def render_covers(name,date_str,color,output_dir="./test",is_16_9=True,is_4_3=True):
    # 16:9 封面
    output={"16_9":None,"4_3":None}
    if is_16_9:
        rendercover_with_manimgl(
            name,
            date_str,
            color,
            str(output_dir),
            is_open=False,
            extra_args=["-r", "1920x1080"],
        )
        src = Path(output_dir) / "cover.png"
        dst = Path(output_dir) / f"cover16_9_{date_str}.png"
        if dst.exists():
            os.remove(dst)
        src.rename(dst)
        output["16_9"]=dst

    # 4:3 封面
    if is_4_3:
        rendercover_with_manimgl(
            name,
            date_str,
            color,
            str(output_dir),
            is_open=False,
            extra_args=["-r", "1440x1080"],
        )
        src = Path(output_dir) / "cover.png"
        dst = Path(output_dir) / f"cover4_3_{date_str}.png"
        if dst.exists():
            os.remove(dst)
        src.rename(dst)
        output["4_3"]=dst
    return output


def upload_cover(
    account=3546637425182939,
    image_path="D:/DanmakuRender/test/cover_12月1日.png"
    ):
    url_upcover="https://member.bilibili.com/x/vu/web/cover/up"
    headers={
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Origin": "https://member.bilibili.com",
        "Referer": "https://member.bilibili.com/platform/upload/video/frame",
        "User-Agent": "Mozilla/5.0"
        }
    cookies=get_cookies(account)

    t = int(__import__("time").time() * 1000)

    with open(image_path, "rb") as f:
        import base64
        b64 = base64.b64encode(f.read()).decode()
    # print(b64[:100])
    r = requests.post(
        url_upcover,
        params={"t":t , "csrf":cookies["bili_jct"]},
        headers=headers,
        cookies=cookies,
        data={"cover": f"data:image/jpeg;base64,{b64}"},
    )
    j = r.json()
    if j.get('code',-1) !=0:
        print(f"错误:{j}")
        return
    else:
        im_url=j.get('data').get('url')
        # print(im_url)
        return im_url

def run_biliuprs(
        file_path,
        account,
        cover,title,desc,dynamic,extra_args,tag,source,limit,
        no_reprint,open_elec,copyright,dtime,tid,
        is_upload=False,
        quiet=True,
        show_cmd=True,
        **kwargs
    ):
    cmd=[
    'tools/biliup.exe',
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
    '--open-elec',open_elec,
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
        pprint(cmd)
    if not is_upload:
        print("不进行上传")
        return None
    if quiet:
        print("正在上传......")
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
        sp.run(cmd)
        return None

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

    with open(f"./configs/DMR-{taskname}.yml", "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    common_event_args=data.get("common_event_args")
    download_args=data.get("download_args")
    if suffix == "danmaku":           # 弹幕版
        upload_args = data["upload_args"]["dm_video"]
    elif suffix == "transcode":       # 转码后
        upload_args = data["upload_args"]["src_video"]
    else:
        raise RuntimeError(f"未知视频类型，无法决定 upload_args：{taskname}")

    return taskname,common_event_args,download_args,upload_args

def replace_args_keywords(taskname,common_event_args,download_args,upload_args):
    live_time_path=download_args.get("output_dir")
    st=read_live_time_from_path(live_time_path)
    et=read_live_time_from_path(live_time_path,is_start=False)
    st_dict={
        "year": st.year,
        "month": st.month,
        "day": st.day,
        "hour": st.hour,
        "minute": st.minute,
    }
    et_dict={
        "year": et.year,
        "month": et.month,
        "day": et.day,
        "hour": et.hour,
        "minute": et.minute,
    }
    kw_info = {
    "streamer":{"name":taskname,"url":download_args.get("url")},
    "stime": st_dict,
    "etime": et_dict,
    "title": "", # 直播间的标题未知
    "totaltime":format_duration(st,et),
    }

    is_render_cover=common_event_args.get("cover_args",{}).get("is_render_cover")
    if is_render_cover:
        name=common_event_args.get("cover_args",{}).get("name")
        output_dir=common_event_args.get("cover_args",{}).get("output_dir")
        name_color=common_event_args.get("cover_args",{}).get("name_color")
        stime=read_live_time_from_path(download_args.get("output_dir"))
        time=f"{stime.month}月{stime.day}日"
        from DMR.utils.render_with_manimgl import rendercover_with_manimgl
        rendercover_with_manimgl(name, time,name_color,output_dir)

    title=replace_keywords(upload_args.get("title"),kw_info)
    desc=replace_keywords(upload_args.get("desc"),kw_info)
    dynamic=replace_keywords(upload_args.get("dynamic"),kw_info)
    add_livetime_todesc=replace_keywords(upload_args.get("add_livetime_todesc"),kw_info)
    upload_args["title"]   = title
    upload_args["desc"]    = desc
    upload_args["dynamic"] = dynamic
    upload_args["add_livetime_todesc"] = add_livetime_todesc

    return taskname,common_event_args,download_args,upload_args

def add_time_and_to_list(file_path,bvid:str):
    _,_,_,upload_args=replace_args_keywords(*file_to_args(file_path))
    account            = upload_args.get("account")
    add_livetime_todesc= upload_args["add_livetime_todesc"]
    sectionId          = upload_args.get("sectionId")
    add_livetime(bvid,add_livetime_todesc,account)
    add_to_list(bvid,sectionId,account)

def upload_video(file_path,re_add_time_and_to_list=False):
    _,_,_,upload_args=replace_args_keywords(*file_to_args(file_path))
    bvid=run_biliuprs(
        file_path,
        is_upload=True,
        quiet=True if re_add_time_and_to_list else False,
        **upload_args)
    if re_add_time_and_to_list:
        if bvid is not None:
            add_time_and_to_list(file_path,bvid)
        else:
            print("获取bvid失败")

def merge_files(files:list):
    _,_,download_args,_=file_to_args(files[0])
    atime=read_live_time_from_path(download_args.get("output_dir"))
    output_path,_=merge_amplify_mp4(files,atime)
    return output_path

def replace_with_new_cover(
    sectionId="shou",
    account=ACCOUNT,
    start_number=0
    ):
    section_info=get_section_info(sectionId,account)
    length=len(section_info)
    for n in range(start_number,length):
        bvid     = section_info[n]["bvid"]
        name     = section_info[n]["name"]
        date_str = section_info[n]["date_str"]
        color    = section_info[n]["color"]
        if not (name and date_str and color):
            continue

        out_path=render_covers(name,date_str,color)
        cover_16_9=out_path.get("16_9")
        cover_4_3=out_path.get("4_3")
        url_16_9=upload_cover(account,cover_16_9)
        time.sleep(3)
        url_4_3 =upload_cover(account,cover_4_3)

        cookies=get_cookies(account)
        headers = build_headers()
        params = {"csrf": cookies["bili_jct"]}
        url_edit='https://member.bilibili.com/x/vu/web/edit'
        payload = build_edit_payload(bvid,account)
        payload["cover"]   =url_16_9
        payload["cover43"] =url_4_3

        r=requests.post(
            url_edit,
            headers=headers,
            cookies=cookies,
            params=params,
            data=json.dumps(payload).encode("utf-8"))
        j = r.json()
        if j.get("code") == 0:
            print(f"{n}成功修改封面",bvid,name,date_str,color)
        else:
            print(f"{n}修改封面失败:{j}",bvid,name,date_str,color)
        time.sleep(3)


if __name__ == "__main__":
    # file_path1=r"D:\DanmakuRender\不可一世杀手（弹幕版）\12月4日001_merged_amplified.mp4"
    # file_path2=r"D:\DanmakuRender\月亮3（弹幕版）\12月3日_merged_amplified.mp4"
    # upload_video(file_path2,re_add_time_and_to_list=True)
    f=Path("D:/DanmakuRender/不可一世杀手（弹幕版）/12月4日001_merged.mp4")
    amplify_to_minus1db(f,remover=False,extra_gain=20)
    # pass