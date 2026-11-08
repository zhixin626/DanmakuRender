import json
import os
import urllib.parse
import urllib.request

from DMR.Login.cookiemgr import PLATFORM_PREFIXES
from . import common

PACK_DIR = os.path.join("emoji_pack", "bilibili")
MANIFEST = os.path.join(PACK_DIR, "bilibili_emoji.json")
API = "https://api.live.bilibili.com/xlive/web-ucenter/v2/emoticon/GetEmoticons"
BUVID_API = "https://api.bilibili.com/x/frontend/finger/spi"
# 实测：账号级的"通用表情"+"emoji表情"两个包不随房间号变化，只有"UP主粉丝团"这种粉丝勋章包
# 才跟房间挂钩；为了速度先固定查一个房间，够用。
ROOM_ID = "24486091"


def _get_buvid():
    """B站风控(2025-06后)要求cookie里带buvid3/buvid4才会给全量表情包(含[dog]等老式表情)，
    否则只给缩水的默认集。这个接口不需要登录，现场生成一份就行，跟 DMR/LiveAPI/danmaku/bilibili.py
    里弹幕连接用的是同一个套路。"""
    req = urllib.request.Request(BUVID_API, headers=common.UA)
    data = json.load(urllib.request.urlopen(req, timeout=10))
    d = data.get('data', {})
    return d.get('b_3'), d.get('b_4')


def _load_cookie():
    path = common.latest_cookie_file(PLATFORM_PREFIXES['bilibili'])
    if not path:
        raise FileNotFoundError(f"找不到 B站 cookie(.login_info/{PLATFORM_PREFIXES['bilibili']}*.json)")
    d = json.load(open(path, encoding="utf-8"))
    cks = d.get("cookie_info", {}).get("cookies")
    cookie = "; ".join(f"{c['name']}={c['value']}" for c in cks) if cks \
        else "; ".join(f"{k}={v}" for k, v in d.items() if isinstance(v, str))
    if 'buvid3' not in cookie or 'buvid4' not in cookie:
        b3, b4 = _get_buvid()
        cookie += f"; buvid3={b3}; buvid4={b4}"
    return cookie, path.name


def _fetch_room(room_id, cookie):
    """返回该房间"通用+专属"表情的 name->url 映射"""
    url = API + "?" + urllib.parse.urlencode({"platform": "pc", "room_id": room_id})
    req = urllib.request.Request(url, headers={**common.UA, "Cookie": cookie})
    data = json.load(urllib.request.urlopen(req, timeout=20))
    if data.get("code") != 0:
        raise RuntimeError(f"房间{room_id} 接口返回 code={data.get('code')} msg={data.get('message')}")
    result = {}
    for pkg in data.get("data", {}).get("data", []):
        for e in pkg.get("emoticons", []):
            name, img_url = e.get("emoji"), e.get("url")
            if name and img_url:
                result[name] = img_url
    return result


def check():
    """返回 {missing, existing_count, unique_count, cookie_used}"""
    cookie, cookie_used = _load_cookie()
    merged = _fetch_room(ROOM_ID, cookie)

    os.makedirs(PACK_DIR, exist_ok=True)
    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

    missing, extra, existing_count, unique_count = common.diff_pack(merged, PACK_DIR)
    return {
        'missing': missing,
        'extra': extra,
        'existing_count': existing_count,
        'unique_count': unique_count,
        'cookie_used': cookie_used,
    }


def download(items):
    return common.download_missing(items, PACK_DIR, headers=common.UA)


def delete_extras(items):
    with open(MANIFEST, encoding="utf-8") as file:
        return common.delete_extras(json.load(file), PACK_DIR, items)


if __name__ == '__main__':
    import sys
    room_id = sys.argv[1] if len(sys.argv) > 1 else ROOM_ID
    cookie, cookie_used = _load_cookie()
    print(f"使用cookie: {cookie_used} | 房间: {room_id}", file=sys.stderr)
    url = API + "?" + urllib.parse.urlencode({"platform": "pc", "room_id": room_id})
    req = urllib.request.Request(url, headers={**common.UA, "Cookie": cookie})
    raw = urllib.request.urlopen(req, timeout=20).read()
    sys.stdout.buffer.write(raw)   # 原样输出接口返回的字节，不重新格式化，方便跟浏览器抓到的response对比
