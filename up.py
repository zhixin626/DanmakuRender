import subprocess as sp
import os
from datetime import datetime
from pathlib import Path
import re
from pprint import pprint
from send2trash import send2trash
import requests
import json
from functools import reduce
from hashlib import md5
import urllib.parse
import time

def concat(files,out_path):
    with open("filelist.txt", "w", encoding="utf-8") as f:
        for file in files:
            f.write(f"file '{file}'\n")

    cmd = [
            'tools/ffmpeg.exe', "-y",
            "-f", "concat",
            # "-loglevel", "error",
            "-safe", "0",
            "-i", "filelist.txt",
            "-c", "copy",
            "-movflags", "+faststart",
            out_path,
            ]
    sp.run(cmd)
def up_file(file_path,task_name="不可一世杀手",url='https://live.douyin.com/zcw199608'):
    current_time=datetime.now()
    year = current_time.year
    month = current_time.month
    day = 6
    biliuprs=[
    'tools/biliup.exe',
    '-u','.login_info/3546637425182939.json',
    'upload',
    '--copyright','1',
    '--cover','',
    '--desc',
    f'{task_name} 的直播回放\n标题：无我之境 \n时间：{year}年{month}月{day}日\n直播地址：{url} \n录制工具：https://github.com/SmallPeaches/DanmakuRender',
    '--dtime','0',
    '--dynamic',
    f'松弟们!手儿{year}年{month}月{day}日的直播回放来了!',
    '--limit','3',
    '--no-reprint','1',
    '--open-elec','1',
    '--source','',
    '--tag',f'直播回放,{task_name}',
    '--tid','65',
    '--title',f'不可一世杀手直播回放{year}.{month}.{day}(大弹幕版)',
    '--extra-fields','{"is_only_self":0}',
    file_path
]
    print(biliuprs)
    sp.run(biliuprs)

# files=[
# 'D:\\DanmakuRender\\不可一世杀手（弹幕版）\\1.mp4',
# 'D:\\DanmakuRender\\不可一世杀手（弹幕版）\\不可一世杀手-2025.08.29.01点18分（弹幕版）.mp4'
# ]
# out_path='D:\\DanmakuRender\\不可一世杀手（弹幕版）\\final.mp4'
# concat(files,out_path)
def detect(file):
    cmd = [
            'tools/ffmpeg.exe',
            '-hide_banner',
            '-i',file,
            '-vn',
            '-af','volumedetect',
            '-f','null','-'
            ]
    r = sp.run(cmd, text=True, capture_output=True ,encoding='utf-8')
    m = re.search(r'max_volume:\s*([-\d\.]+)\s*dB', r.stderr)
    if not m:
        raise RuntimeError("未检测到 max_volume（可能没有音轨或滤镜未运行）")
    return float(m.group(1))
def amplify_to_minus1db(file):
    peak = detect(file)
    if peak is None:
        raise RuntimeError("未检测到 max_volume")

    gain_db = -1 - peak
    if gain_db < 0:
        gain_db = 0
    out = Path(file).with_name(Path(file).stem + "_amplified.mp4")
    cmd = [
        'tools/ffmpeg.exe',
        '-i', file,
        '-af', f'volume={gain_db:.2f}dB,alimiter=limit=-1dB',
        '-c:v', 'copy',
        str(out)
    ]
    sp.run(cmd)
    print(f"检测峰值 {peak:.2f} dBFS → 放大 {gain_db:.2f} dB → 输出: {out}")




def get_cookies(account=3546637425182939):
    login_json=fR"D:\DanmakuRender\.login_info\{account}.json"
    with open(login_json,'r',encoding='utf-8') as f:
        data=json.load(f)
    cookies={}
    for c in data['cookie_info']['cookies']:
        name=c.get('name')
        value=c.get('value')
        if name and value:
            cookies[name]=value
    return cookies
def build_headers():
    return {
    "Content-Type": "application/json; charset=UTF-8",
    "Origin": "https://member.bilibili.com",
    "Referer": "https://member.bilibili.com/platform/series/manager",
    "User-Agent": "Mozilla/5.0"
    }
def add_to_list(bvid,sectionId):
    sectionId_leng=7184492
    sectionId_shou=7184423
    if isinstance(sectionId, (int, float)):  # 数字直接用
        pass
    elif isinstance(sectionId, str):
        if sectionId.lower() == "shou":
            sectionId = sectionId_shou
        elif sectionId.lower() == "leng":
            sectionId = sectionId_leng
        elif sectionId.isdigit():  # 如果是数字字符串
            sectionId = int(sectionId)
        else:
            raise ValueError(f"未知的 sectionId 标识: {sectionId!r}")
    else:
        raise TypeError(f"sectionId 类型不合法: {type(sectionId)}")

    cookies=get_cookies()
    headers = build_headers()
    info,cid,aid,title=get_info(bvid)
    payload = {
    "sectionId": sectionId,
    "episodes": [{
        "title": title, # 合集里的标题
        "cid": cid,     # 分p视频的id
        "aid": aid
        }]
    }
    params = {"csrf": cookies["bili_jct"]}
    url = "https://member.bilibili.com/x2/creative/web/season/section/episodes/add"
    r=requests.post(url,headers=headers,cookies=cookies,
        params=params,data=json.dumps(payload).encode("utf-8"))
    rj=r.json()
    if rj.get("code") == 0:
        print(f"成功添加到合集{sectionId}")
    else:
        print("失败:", rj)

def get_info(bvid):
    cookies=get_cookies()
    headers = build_headers()
    url_view='https://member.bilibili.com/x/vupre/web/archive/view'
    r1=requests.get(url_view,params={'bvid':bvid},headers=headers, cookies=cookies)
    info = r1.json()["data"]
    videos= r1.json()["data"]['videos'][0]
    cid=videos['cid']
    aid=videos['aid']
    title=videos['title']
    return info,cid,aid,title

def add_offlinetime(bvid: str, offline_time: str) :
    def insert_offline_time(desc: str, offline_time: str) -> str:
        pattern = r"(开播时间：.*(?:\n|$))"
        insert_text = f"下播时间：{offline_time}\n"
        # 如果找到了就替换，否则原样返回
        new_desc, count = re.subn(pattern, lambda m: m.group(1) + insert_text, desc)
        if count == 0:
            # 如果没有找到匹配行，就在末尾补上
            new_desc = desc.rstrip() + "\n" + insert_text
        return new_desc

    cookies=get_cookies()
    headers = build_headers()
    params = {"csrf": cookies["bili_jct"]}
    url_view='https://member.bilibili.com/x/vupre/web/archive/view'
    url_edit='https://member.bilibili.com/x/vu/web/edit'
    r1=requests.get(url_view,params={'bvid':bvid},headers=headers, cookies=cookies)
    info = r1.json()["data"]
    arc = info["archive"]
    payload = {
        "cover": arc["cover"].replace("http:", "").replace("https:", ""),
        "cover43": arc["cover43"].replace("http:", "").replace("https:", ""),
        "ai_cover": arc["ai_cover"],
        "title": arc["title"],
        "copyright": arc["copyright"],
        "human_type2": arc["human_type2"]["id"],
        "tid": arc["tid"],
        "tag": arc["tag"],
        "desc": insert_offline_time(arc["desc"],offline_time),
        "dynamic": arc["dynamic"],
        "recreate": -1,
        "interactive": arc["interactive"],
        "videos": [
            {
                "filename": v["filename"],
                "title": v["title"],
                "desc": v["desc"],
                "cid": v["cid"]
            } for v in info["videos"]
        ],
        "aid": arc["aid"],
        "handle_staff": False,
        "mission_id": arc["mission_id"],
        "is_only_self": arc["is_only_self"],
        "watermark": {"state": info["watermark"]["state"]},
        "no_reprint": arc["no_reprint"],
        "is_360": arc["is_360"],
        "dolby": arc["is_dolby"],
        "lossless_music": arc["lossless_music"],
        "new_web_edit": 1,
        "topic_grey": 1,
        "act_reserve_create": 0,
        "subtitle": {"open": 0, "lan": ""},
        "web_os": 1,
        "csrf": cookies["bili_jct"]
    }
    # pprint(payload1)
    r=requests.post(url_edit,headers=headers,cookies=cookies,params=params,
        data=json.dumps(payload).encode("utf-8"))
    rj = r.json()      # 或者：rj = json.loads(r.text)
    if rj.get("code") == 0:
        print("成功添加下播时间")
    else:
        print("失败:", rj)


if __name__ == '__main__':
    bvid='BV1hbkfBdEMF'
    time='2025年'
    add_to_list(bvid,'leng')
    add_offlinetime(bvid,'2026')
    # info,cid,aid,title=get_info(bvid)
    # print(cid)
    # print(aid)
    # print(title)



