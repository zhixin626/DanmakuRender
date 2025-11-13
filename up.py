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
import time,yaml
import logging

from DMR.Task.merge_mp4 import read_live_time_from_path
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)
import sys
sys.path.append(str(Path(r"D:\DanmakuRender\DMR\Task")))
from merge_mp4 import *

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
def build_like_browser_headers(referer: str, origin: str):
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Origin": origin,
        "Referer": referer,
        "Connection": "keep-alive",
        "X-Requested-With": "XMLHttpRequest",
        # X-CSRF-Token 与 cookie 里的 bili_jct 一致（很多接口可选但对风控有帮助）
        # 下面在发请求前再 set 一次，确保拿到最新 cookies
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
    archive= r1.json()["data"]['archive']
    cid=videos['cid']
    aid=videos['aid']
    title=archive['title']
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


def reorder_section_once(section_id: int, account: int, mode: str = "first_to_last"):
    """
    根据当前 section 的顺序，执行一次“首尾互换”（first_to_last / last_to_first）并提交到 B站接口。
    :param section_id: 分区ID (sectionId)
    :param account: 账号ID，用于读取 cookies
    :param mode: "first_to_last" 或 "last_to_first"
    :return: 提交后的返回JSON
    """
    url_section = "https://member.bilibili.com/x2/creative/web/season/section"
    url_sort = 'https://member.bilibili.com/x2/creative/web/season/section/edit'

    cookies = get_cookies(account)
    headers = build_headers()

    # 拉取分区信息
    r = requests.get(
        url_section,
        headers=headers,
        cookies=cookies,
        params={'id': section_id}
    )
    j = r.json()

    # 修改：增加返回码校验并在失败时记录日志
    if j.get('code') != 0:
        logger.error(f"获取分区信息失败: {j}")
        return j

    data = j.get('data') or {}
    # 修改：这里兼容 data['sorts'] 和 data['episodes'] 两种字段名
    episodes = data.get('sorts') or data.get('episodes') or []
    section = data.get('section') or {}
    section_id = section.get('id', section_id)  # 如果返回里有，以返回为准
    season_id = section.get('seasonId')

    if not episodes:
        logger.error("当前分区无可排序的视频（episodes 为空）")
        return {'code': -1, 'message': 'no episodes'}

    # 原地定义的 reorder 子函数，基本保持你原逻辑
    def reorder(mode_local="last_to_first"):
        if mode_local == "last_to_first":
            reordered = [episodes[-1]] + episodes[:-1]
        elif mode_local == "first_to_last":
            reordered = episodes[1:] + [episodes[0]]
        elif mode_local in ("keep", "no_change", "original"):
            reordered = episodes[:]  # 顺序不变
        else:
            raise ValueError(f"未知的排序模式: {mode_local}")

        new_sorts = [{'id': e['id'], 'sort': i + 1} for i, e in enumerate(reordered)]
        return new_sorts

    payload = {
        'section': {
            'id': section_id,
            'seasonId': season_id,
            'title': section.get('title', '正片'),
            'type': section.get('type', 1),
        },
        'sorts': reorder(mode)  # 使用入参 mode
    }

    r2 = requests.post(
        url_sort,
        params={"csrf": cookies["bili_jct"]},
        headers=headers,
        cookies=cookies,
        json=payload
    )
    # 修改：解析 JSON 并做成功/失败日志
    try:
        rj = r2.json()
    except Exception:
        logger.error(f"排序提交失败(非JSON响应): {r2.text}")
        return {'code': -1, 'message': 'non-json response', 'raw': r2.text}

    if rj.get("code") == 0:
        # 修改：按你的要求增加成功信息
        logger.info(f"成功调整合集分区顺序（sectionId={section_id}, mode={mode}）")
    else:
        logger.error(f"调整失败: {rj}")

    return rj
def format_duration(start: datetime, end: datetime) -> str:
    delta = end - start
    total_seconds = int(delta.total_seconds())
    if total_seconds < 0:
        total_seconds = -total_seconds  # 允许反向计算

    days, rem = divmod(total_seconds, 86400)   # 一天 = 86400 秒
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)

    if days > 0:
        return f"{days}天{hours}小时{minutes}分钟"
    elif hours > 0:
        return f"{hours}小时{minutes}分钟"
    elif minutes > 0:
        return f"{minutes}分钟"
    else:
        return f"{seconds}秒"
import requests

URL_SECTION      = "https://member.bilibili.com/x2/creative/web/season/section"
URL_EPISODE_EDIT = "https://member.bilibili.com/x2/creative/web/season/section/episode/edit"
URL_VIEW = "https://member.bilibili.com/x/vupre/web/archive/view"
URL_EDIT = "https://member.bilibili.com/x/vu/web/edit"


def replace_ascii_pipe(title: str) -> str:
    """把 ASCII 竖线 '|' 全部替换为全角 '｜'（不动其它字符）。"""
    return title.replace("|", "｜")

def build_episode_edit_payload(episode: dict, section: dict, *, new_title: str) -> dict:
    """
    只负责生成 payload，不发请求。
    依据通常返回字段：id/aid/cid/(sort|order)、section.id、section.seasonId。
    注意：B站接口的字段名可能随版本变动；这里尽量兼容常见字段。
    """
    eid   = episode.get("id")
    aid   = episode.get("aid")
    cid   = episode.get("cid")
    order = episode.get("order", episode.get("sort", 0))
    secid = section.get("id")
    seid  = section.get("seasonId") or section.get("season_id")

    payload = {
        "id": eid,
        "aid": aid,
        "cid": cid,
        "order": order,            # 有些接口叫 order，有些叫 sort；此处用 order
        "seasonId": seid,
        "sectionId": secid,
        "title": new_title,
        # 如果接口需要 sort 而不是 order，可以同时带上，以防万一
        "sort": order,
    }
    return payload

def fetch_section_detail(section_id: int, account: int, logger=None, with_archive_titles: bool = False):
    cookies = get_cookies(account)
    headers = build_headers()
    r = requests.get(URL_SECTION, headers=headers, cookies=cookies, params={"id": section_id})
    try:
        j = r.json()
    except Exception:
        if logger: logger.error(f"获取分区信息非JSON响应：{r.text[:200]}")
        return (None, None, None, cookies, headers) if not with_archive_titles else (None, None, None, cookies, headers, [])

    if j.get("code") != 0:
        if logger: logger.error(f"获取分区信息失败：{j}")
        return (None, None, j, cookies, headers) if not with_archive_titles else (None, None, j, cookies, headers, [])

    data    = j.get("data") or {}
    section = data.get("section") or {}
    # 兼容字段名：episodes / sorts
    episodes = data.get("episodes") or data.get("sorts") or []

    if with_archive_titles:
        archive_titles = [e.get("archiveTitle", "") for e in episodes]
        return episodes, section, j, cookies, headers, archive_titles

    return episodes, section, j, cookies, headers


def replace_pipes_in_titles(section_id: int, account: int, *, dry_run: bool = True, logger=None):
    """
    遍历 section 内所有视频标题：
    - 找到含有半角竖线 '|' 的条目；
    - 把它们替换为全角 '｜'；
    - 调用 episode/edit 接口更新标题。
    返回一个结果列表，包含每个尝试的条目及接口返回。
    """
    episodes, section, j, cookies, headers = fetch_section_detail(section_id, account, logger=logger)
    if episodes is None:
        return {"code": -1, "message": "fetch section failed", "raw": j}

    if not episodes:
        msg = "当前分区无可处理的视频（episodes 为空）"
        if logger: logger.warning(msg)
        return {"code": 0, "message": msg, "updated": 0, "details": []}

    details = []
    updated = 0
    csrf = cookies.get("bili_jct")
    params = {"csrf": csrf} if csrf else {}

    for ep in episodes:
        old_title = ep.get("title", "")
        if "|" not in old_title:
            continue  # 不含半角竖线，跳过

        new_title = replace_ascii_pipe(old_title)
        payload = build_episode_edit_payload(ep, section, new_title=new_title)

        if logger:
            logger.info(f"[pipe-fix] id={ep.get('id')} 旧标题: {old_title} -> 新标题: {new_title}")

        if dry_run:
            details.append({"id": ep.get("id"), "old": old_title, "new": new_title, "dry_run": True})
            continue

        # 真正提交
        time.sleep(2)
        r2 = requests.post(
            URL_EPISODE_EDIT,
            headers=headers,
            cookies=cookies,
            params=params,
            json=payload
        )
        try:
            rj = r2.json()
        except Exception:
            rj = {"code": -1, "message": "non-json response", "raw": r2.text}

        if rj.get("code") == 0:
            updated += 1
            if logger:
                logger.info(f"[pipe-fix] 更新成功: id={ep.get('id')}")
        else:
            if logger:
                logger.error(f"[pipe-fix] 更新失败: id={ep.get('id')} resp={rj}")

        details.append({"id": ep.get("id"), "old": old_title, "new": new_title, "resp": rj})

    return {"code": 0, "message": "ok", "updated": updated, "details": details, "dry_run": dry_run}
def edit_title_pipes(bvid: str = None, aid: int = None, logger=None):
    """
    仅修改稿件标题中的半角竖线为全角竖线。其余字段保持不变。
    可用 bvid 或 aid 其中之一（优先 bvid）。返回接口 JSON。
    """
    if not bvid and not aid:
        raise ValueError("需要提供 bvid 或 aid")

    cookies = get_cookies()
    headers = build_headers()
    params_csrf = {"csrf": cookies["bili_jct"]}

    # 1) 拉取稿件详情
    params_view = {"bvid": bvid}
    r1 = requests.get(URL_VIEW, params=params_view, headers=headers, cookies=cookies)
    j1 = r1.json()
    if j1.get("code") != 0:
        if logger: logger.error(f"[title-fix] 获取稿件失败: {j1}")
        return j1

    info = j1["data"]
    arc = info["archive"]

    old_title = arc["title"]
    new_title = replace_ascii_pipe(old_title)
    if old_title == new_title:
        # 无需修改
        return {"code": 0, "message": "no change", "skip": True, "title": old_title}

    # 2) 组装 payload（沿用你原来的字段，除了 title 改为 new_title）
    payload = {
        "cover": arc["cover"].replace("http:", "").replace("https:", ""),
        "cover43": arc["cover43"].replace("http:", "").replace("https:", ""),
        "ai_cover": arc["ai_cover"],
        "title": new_title,                    # ← 只改这里
        "copyright": arc["copyright"],
        "human_type2": arc["human_type2"]["id"],
        "tid": arc["tid"],
        "tag": arc["tag"],
        "desc": arc["desc"],                  # 不改简介
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
        "csrf": cookies["bili_jct"],
    }

    # 3) 提交前歇 2 秒，降低触发限频
    time.sleep(2)

    r = requests.post(
        URL_EDIT,
        headers=headers,
        cookies=cookies,
        params=params_csrf,
        data=json.dumps(payload).encode("utf-8")
    )
    try:
        rj = r.json()
    except Exception:
        rj = {"code": -1, "message": "non-json response", "raw": r.text}

    if logger:
        if rj.get("code") == 0:
            logger.info(f"[title-fix] 标题已更新：{old_title} -> {new_title}")
        else:
            logger.error(f"[title-fix] 更新失败：{rj}")

    return rj

def replace_pipes_in_titles_via_edit(section_id: int, account: int, *, dry_run: bool = True, logger=None):
    """
    遍历 section 内所有视频标题：
    - 找到含有半角竖线 '|' 的条目；
    - 用 URL_VIEW + URL_EDIT 流程把标题里的 '|' → '｜'；
    - 默认 dry_run 只返回计划变更，不提交。
    """
    episodes, section, j, cookies, headers,old_titles = fetch_section_detail(section_id, account, logger=logger,with_archive_titles=True)
    if episodes is None:
        return {"code": -1, "message": "fetch section failed", "raw": j}

    if not episodes:
        msg = "当前分区无可处理的视频（episodes 为空）"
        if logger: logger.warning(msg)
        return {"code": 0, "message": msg, "updated": 0, "details": [], "dry_run": dry_run}

    details, updated = [], 0

    for ep,old_title in zip(episodes,old_titles):
        if "|" not in old_title:
            continue

        # 可能拿得到 bvid，也可能只有 aid；先取 bvid，缺失就用 aid
        bvid = ep.get("bvid") or ep.get("bVid") or None
        aid = ep.get("aid")

        new_title = replace_ascii_pipe(old_title)
        if dry_run:
            details.append({"id": ep.get("id"), "bvid": bvid, "aid": aid, "old": old_title, "new": new_title, "dry_run": True})
            if logger:
                logger.info(f"[old_title-fix][dry-run] id={ep.get('id')} {old_title} -> {new_title}")
            continue

        resp = edit_title_pipes(bvid=bvid, aid=aid, logger=logger)
        details.append({"id": ep.get("id"), "bvid": bvid, "aid": aid, "old": old_title, "new": new_title, "resp": resp})
        if resp.get("code") == 0 and not resp.get("skip"):
            updated += 1

    return {"code": 0, "message": "ok", "updated": updated, "details": details, "dry_run": dry_run}
if __name__ == '__main__':
    # 先看计划（不提交）
    # res = replace_pipes_in_titles_via_edit(section_id=7184423, account=3546637425182939, dry_run=True, logger=logger)
    # 确认后执行（提交）
    # res = replace_pipes_in_titles_via_edit(section_id=7184423, account=3546637425182939, dry_run=False, logger=logger)



