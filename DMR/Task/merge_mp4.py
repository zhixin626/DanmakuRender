import subprocess, os, json
from pathlib import Path
import tempfile
from typing import List, Optional, Tuple, Dict, Union
from datetime import datetime
import re
from send2trash import send2trash
import requests
import logging
logger = logging.getLogger(__name__)

def _probe_media(ffprobe: str, path: str):
    """
    用 ffprobe 获取基本信息：duration（秒，float），分辨率（width,height），
    以及格式/比特率等。字段缺失时做容错。
    """
    # 取视频流宽高 + 容器时长
    cmd = [
        ffprobe,
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-show_entries", "format=duration,bit_rate,format_name",
        "-of", "json",
        path
    ]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
        data = json.loads(out.decode("utf-8", errors="ignore"))
    except subprocess.CalledProcessError as e:
        logger.warning("ffprobe 失败：%s", e.output.decode("utf-8", errors="ignore"))
        data = {}
    except Exception as e:
        logger.warning("解析 ffprobe 输出失败：%s", e)
        data = {}

    width = height = None
    duration = None
    bit_rate = None
    fmt = None

    try:
        s0 = (data.get("streams") or [{}])[0]
        width = s0.get("width")
        height = s0.get("height")
    except Exception:
        pass

    try:
        fmt_dict = data.get("format") or {}
        # ffprobe 的 duration 是字符串
        duration = float(fmt_dict.get("duration")) if fmt_dict.get("duration") else None
        bit_rate = int(fmt_dict.get("bit_rate")) if fmt_dict.get("bit_rate") else None
        fmt = fmt_dict.get("format_name")
    except Exception:
        pass

    return {
        "duration": duration,                 # 秒（float）或 None
        "resolution": (width, height) if width and height else None,
        "width": width,
        "height": height,
        "bit_rate": bit_rate,                 # 整数（bps）或 None
        "format": fmt,                        # 例如 "mov,mp4,m4a,3gp,3g2,mj2"
        "path": str(path),
        "size": os.path.getsize(path) if os.path.exists(path) else None,
    }

def read_live_time_from_path(path, is_start=True):
    path = Path(path)
    logger.debug(f'放置开下播时间txt文件的文件夹是{path}')
    txt_path = path / ("_livestart_times.txt" if is_start else "_liveend_times.txt")
    try:
        if txt_path.exists():
            with open(txt_path, "r", encoding="utf-8") as f:
                last_line = None
                for line in f:
                    if line.strip():
                        last_line = line.strip()
            if last_line:
                try:
                    time = datetime.fromisoformat(last_line)
                except Exception:
                    time = datetime.now()
                    if logger:
                        logger.warning(f"文件 {txt_path} 最后一行 '{last_line}' 解析失败，使用当前时间。")
                else:
                    return time
            else:
                time = datetime.now()
                if logger:
                    logger.warning(f"文件 {txt_path} 内容为空，使用当前时间。")
        else:
            time = datetime.now()
            if logger:
                logger.warning(f"文件 {txt_path} 不存在，使用当前时间。")
    except Exception as e:
        time = datetime.now()
        if logger:
            logger.warning(f"读取 {txt_path} 出错：{e}，使用当前时间。")

    return time
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

def _gen_autoname(file_path: Path) -> Path:
    """生成 11月2日_merged[(-NNN)].mp4 的不重名文件路径"""
    base_dir=file_path.parent
    time = read_live_time_from_path(base_dir,is_start=True)
    fname = f"{time.month}月{time.day}日_merged.mp4"
    cand = base_dir / fname
    if cand.exists():
        i = 1
        while True:
            fname2 = f"{time.month}月{time.day}日{i:03d}_merged.mp4"
            cand2 = base_dir / fname2
            if not cand2.exists():
                cand = cand2
                break
            i += 1
    return cand

def merge_amplify_mp4(
    mp4_list: List[str],
    out_path: Optional[str] = None,
    ffmpeg: str = r"tools/ffmpeg.exe",
    ffprobe: str = r"tools/ffprobe.exe",
    return_info: bool = False,   # ← 新增：是否返回媒体信息
    is_amplify: bool = True,
) -> Union[str, Tuple[str, Dict[str, Union[str, int, float, Tuple[int, int]]]]]:
    """
    1 个文件：直接返回原路径；
    多个文件：使用 concat demuxer 极速合并（-c copy，不重编码）。
    当 return_info=True 时，返回 (out_path, info_dict)；否则仅返回 out_path。
    """
    inputs = [str(Path(p)) for p in mp4_list if p]
    if not inputs:
        raise ValueError("mp4_list 为空")

    # === 单文件分支
    if len(inputs) == 1:
        src = Path(inputs[0]).resolve()
        base_dir = src.parent

        # 没给 out_path 就用 _gen_autoname(base_dir)
        if not out_path:
            dst = _gen_autoname(src)
        else:
            dst = Path(out_path)

        # 改名
        if src != dst:
            src.rename(dst)
        out_path = str(dst)

        # 进行音频增益至-1dB-----------------------------------------------------
        if is_amplify:
            try:
                amplified = amplify_to_minus1db(out_path)
                send2trash(out_path)                                # 删除放入垃圾桶
                out_path  = amplified
            except Exception as e:
                logger.warning(f"amplify 失败，已跳过: {e}")
        # 进行音频增益至-1dB-----------------------------------------------------

        if return_info:
            info = _probe_media(ffprobe, out_path)
            return out_path, info
        return out_path

    # === 多文件分支
    if out_path is None:
        out_path = str(_gen_autoname( Path(inputs[0]).resolve() ))
    else:
        out_path = str(Path(out_path))

    # —— 生成 filelist（必须是“可被其他进程读取的命名临时文件”）——
    tmp = tempfile.NamedTemporaryFile(
        prefix="ff_filelist_",
        suffix=".txt",
        mode="w",
        encoding="utf-8",
        delete=False,    # Windows 下要先关闭句柄
    )
    try:
        for p in inputs:
            tmp.write(f"file '{Path(p).resolve().as_posix()}'\n")
        tmp.flush()
        filelist = tmp.name
    finally:
        tmp.close()  # 先关闭，让 FFmpeg 能读取

    try:
        cmd = [
            ffmpeg, "-y",
            "-f", "concat",
            "-loglevel", "error",
            "-safe", "0",
            "-i", filelist,
            "-c", "copy",
            "-movflags", "+faststart",
            out_path,
        ]
        # 正确的 logging 用法（占位符），避免你之前遇到的 logging 报错
        logger.debug("merge_mp4 cmd: %s", cmd)
        subprocess.run(cmd, check=True)

        # 进行音频增益至-1dB-------------------------------------------------
        if is_amplify:
            try:
                amplified = amplify_to_minus1db(out_path)
                send2trash(out_path)                                # 删除放入垃圾桶
                out_path = amplified
            except Exception as e:
                logger.warning(f"amplify 失败，已跳过: {e}")
        # 进行音频增益至-1dB-------------------------------------------------

        if return_info:
            info = _probe_media(ffprobe, out_path)
            return out_path, info
        return out_path

    finally:
        # 删除临时清单
        try:
            os.remove(filelist)
        except OSError:
            pass

def detect(file:str):
    cmd = [
            'tools/ffmpeg.exe',
            '-hide_banner',
            '-i',file,
            '-vn',
            '-af','volumedetect',
            '-f','null','-'
            ]
    r = subprocess.run(cmd, text=True, capture_output=True ,encoding='utf-8')
    m = re.search(r'max_volume:\s*([-\d\.]+)\s*dB', r.stderr)
    if not m:
        raise RuntimeError("未检测到 max_volume（可能没有音轨或滤镜未运行）")
    return float(m.group(1))

def amplify_to_minus1db(file:str):
    peak = detect(file) # 检测最高音量max_volume
    if peak is None:
        raise RuntimeError("未检测到 max_volume")

    gain_db = -1 - peak
    if gain_db < 0:
        gain_db = 0
    out = Path(file).with_name(Path(file).stem + "_amplified.mp4")
    cmd = [
        'tools/ffmpeg.exe',
        '-y',                # ← 覆盖输出，避免交互
        '-nostdin',          # ← 不读取标准输入
        '-hide_banner',
        '-i', file,
        '-af', f'volume={gain_db:.2f}dB,alimiter=limit=-1dB',
        '-c:v', 'copy',
        '-c:a', 'aac',
        '-movflags', '+faststart',
        str(out)
    ]
    logger.info('开始进行音频增益')
    result=subprocess.run(cmd, text=True, capture_output=True ,encoding='utf-8')
    if result.returncode != 0:
        # 打印一段 stderr 便于排错
        logger.error("ffmpeg 失败：%s", result.stderr.strip().splitlines()[-1] if result.stderr else "未知错误")
        raise RuntimeError("ffmpeg 执行失败")
    logger.info(f"检测峰值 {peak:.2f} dBFS → 放大 {gain_db:.2f} dB → 输出: {out}")
    return str(out)

def get_cookies(account):
    login_json=Rf"D:\DanmakuRender\.login_info\{account}.json"
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

def add_to_list(bvid,sectionId,account):
    sectionId_leng=7184492
    sectionId_shou=7184423
    if isinstance(sectionId, int):  # 数字直接用
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

    cookies=get_cookies(account)
    headers = build_headers()
    info=get_info(bvid,account)
    video= info['videos'][0]
    archive= info['archive']
    payload = {
    "sectionId": sectionId,
    "episodes":
        [{
        "title": archive['title'], # 合集里的标题
        "cid":   video['cid'],     # 分p视频的id
        "aid":   archive["aid"]
        }]
    }
    params = {"csrf": cookies["bili_jct"]}
    url = "https://member.bilibili.com/x2/creative/web/season/section/episodes/add"
    r=requests.post(url,headers=headers,cookies=cookies,
        params=params,data=json.dumps(payload).encode("utf-8"))
    rj=r.json()
    if rj.get("code") == 0:
        logger.info(f"成功添加到合集{sectionId}")
    else:
        logger.info("失败:", rj)

    reorder_section_once(sectionId,account,mode='last_to_first')

def get_info(bvid,account):
    cookies=get_cookies(account)
    headers = build_headers()
    url_view='https://member.bilibili.com/x/vupre/web/archive/view'
    r1=requests.get(url_view,params={'bvid':bvid},headers=headers, cookies=cookies)
    j = r1.json()
    if j.get("code") != 0:
        raise RuntimeError(f"view失败: ")
    info = j["data"]
    return info

def add_livetime(bvid: str, text: str,account) :
    def insert_after_title(desc: str, text: str) -> str:
        pattern = r"(开播时间：.*(?:\n|$))"
        insert_text = f"{text}\n"
        # 如果找到了就替换，否则原样返回
        new_desc, count = re.subn(pattern, lambda m: m.group(1) + insert_text, desc)
        if count == 0:
            # 如果没有找到匹配行，就在末尾补上
            new_desc = desc.rstrip() + "\n" + insert_text
        return new_desc

    cookies=get_cookies(account)
    headers = build_headers()
    params = {"csrf": cookies["bili_jct"]}
    url_edit='https://member.bilibili.com/x/vu/web/edit'
    info=get_info(bvid,account)
    archive = info["archive"]
    payload = {
        "cover": archive["cover"],
        "cover43": archive["cover43"],
        "ai_cover": archive["ai_cover"],
        "title": archive["title"],
        "copyright": archive["copyright"],
        "human_type2": archive["human_type2"]["id"],
        "tid": archive["tid"],
        "tag": archive["tag"],
        "desc": insert_after_title(archive["desc"],text),
        "dynamic": archive["dynamic"],
        "recreate": -1,
        "interactive": archive["interactive"],
        "videos": [
            {
                "filename": v["filename"],
                "title": v["title"],
                "desc": v["desc"],
                "cid": v["cid"]
            } for v in info["videos"]
        ],
        "aid": archive["aid"],
        "handle_staff": False,
        "mission_id": archive["mission_id"],
        "is_only_self": archive["is_only_self"],
        "watermark": {"state": info["watermark"]["state"]},
        "no_reprint": archive["no_reprint"],
        "is_360": archive["is_360"],
        "dolby": archive["is_dolby"],
        "lossless_music": archive["lossless_music"],
        "new_web_edit": 1,
        "topic_grey": 1,
        "act_reserve_create": 0,
        "subtitle": {"open": 0, "lan": ""},
        "web_os": 1,
        "csrf": cookies["bili_jct"]
    }
    r=requests.post(url_edit,headers=headers,cookies=cookies,params=params,
        data=json.dumps(payload).encode("utf-8"))
    rj = r.json()      # 或者：rj = json.loads(r.text)
    if rj.get("code") == 0:
        logger.info("成功添加直播时间到简介")
    else:
        logger.info("失败:", rj)

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
            new_sorts = [{'id': e['id'], 'sort': i + 1} for i, e in enumerate(reordered)]
            return new_sorts
        elif mode_local == "first_to_last":
            reordered = episodes[1:] + [episodes[0]]
            new_sorts = [{'id': e['id'], 'sort': i + 1} for i, e in enumerate(reordered)]
            return new_sorts
        else:
            # 修改：增加非法 mode 的防御
            raise ValueError(f"未知的排序模式: {mode_local}")

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
        logger.info(f"成功调整合集分区顺序:{mode}")
    else:
        logger.error(f"调整失败: {rj}")

    return rj