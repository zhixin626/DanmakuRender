import json
import os

import requests

from DMR.LiveAPI.douyin import douyin_utils
from DMR.Login.cookiemgr import PLATFORM_PREFIXES, load_cookie_dict
from . import common

PACK_DIR = os.path.join("emoji_pack", "douyin")
MANIFEST = os.path.join(PACK_DIR, "douyin_emoji.json")
API = "https://live.douyin.com/aweme/v1/web/emoji/list"


def _load_cookie():
    path = common.latest_cookie_file(PLATFORM_PREFIXES['douyin'])
    if not path:
        raise FileNotFoundError(f"找不到抖音 cookie(.login_info/{PLATFORM_PREFIXES['douyin']}*.json)")
    return load_cookie_dict(path), path.name


def _fetch():
    """账号级接口，不需要房间号，返回该账号能看到的全部表情 name->url 映射"""
    cookies, cookie_used = _load_cookie()
    url = douyin_utils.build_request_url(API, query={'need_all': 'true'})
    headers = douyin_utils.get_headers(extra_cookies=cookies)
    resp = requests.get(url, headers=headers, timeout=10)
    data = resp.json()
    emoji_list = data.get("emoji_list") or data.get("data", {}).get("emoji_list") or []
    result = {}
    for e in emoji_list:
        name = e.get("display_name")                      # 如 "[盖瑞]"
        url_list = (e.get("emoji_url") or {}).get("url_list") or []
        if name and url_list:
            result[name] = url_list[0]
    return result, cookie_used


def check():
    merged, cookie_used = _fetch()
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
