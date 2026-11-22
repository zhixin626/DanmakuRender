# -*- coding: utf-8 -*-
"""
douyu_auth.py
斗鱼登录 cookie 续期：不开浏览器，用 passport 域的长期票据 LTP0 换一套新的 acf_* 登录 cookie。

斗鱼主站的 acf_* 登录 cookie 只有约 6 天有效期（Max-Age=529200），LTP0 能用几个月。
做法参考 bililive-go（src/live/douyu/refresh.go）：带着 LTP0 和 dy_did 请求 safeAuth，
逐跳跟随 302，收集各跳的 Set-Cookie，再用关注列表接口确认登录有效。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import requests

from DMR.Login.cookiemgr import load_cookie_dict, save_cookie_file

SAFE_AUTH_URL = 'https://passport.douyu.com/lapi/passport/iframe/safeAuth'
PROBE_URL = 'https://www.douyu.com/wgapi/livenc/liveweb/follow/top3'
USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
)
# 换票成功后这些登录项必须都有
REQUIRED_KEYS = ('acf_uid', 'acf_auth', 'acf_stk', 'acf_ltkid', 'acf_username', 'acf_biz', 'acf_ct')
EXPIRY_KEYS = {'acf_auth', 'acf_uid', 'acf_stk'}
MAX_HOPS = 8


def _allowed(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or '').lower()
    return parsed.scheme == 'https' and (host == 'douyu.com' or host.endswith('.douyu.com'))


def _exchange(ltp0: str, did: Optional[str]) -> Tuple[int, Dict[str, Tuple[str, Optional[float]]]]:
    """带 LTP0 请求 safeAuth 并逐跳跟随 302，返回最终状态码和收集到的 cookie。"""
    now_ms = str(int(time.time() * 1000))
    url = f'{SAFE_AUTH_URL}?client_id=1&t={now_ms}&_={now_ms}&callback=cb'
    cookie = (f'dy_did={did}; ' if did else '') + f'LTP0={ltp0}'
    headers = {'User-Agent': USER_AGENT, 'Referer': 'https://www.douyu.com/', 'Cookie': cookie}
    collected: Dict[str, Tuple[str, Optional[float]]] = {}
    with requests.Session() as session:
        for _ in range(MAX_HOPS):
            resp = session.get(url, headers=headers, allow_redirects=False, timeout=10)
            for item in resp.cookies:
                if item.value:
                    collected[item.name] = (item.value, item.expires)
            if not resp.is_redirect:
                return resp.status_code, collected
            location = resp.headers.get('Location')
            if not location:
                return resp.status_code, collected
            url = urljoin(url, 'https:' + location if location.startswith('//') else location)
            if not _allowed(url):
                raise RuntimeError(f'续期跳转到了非斗鱼地址：{urlparse(url).hostname}')
            session.cookies.clear()     # 每一跳只带 LTP0/dy_did，和官方页面一致
    return 0, collected


def _probe(cookies: Dict[str, str]) -> Tuple[bool, str]:
    cookie = '; '.join(f'{k}={v}' for k, v in sorted(cookies.items()))
    resp = requests.get(
        PROBE_URL,
        headers={'User-Agent': USER_AGENT, 'Referer': 'https://www.douyu.com/', 'Cookie': cookie},
        timeout=10,
    )
    if resp.status_code != 200:
        raise RuntimeError(f'登录校验请求失败（HTTP {resp.status_code}）')
    try:
        data = resp.json()
    except ValueError:
        raise RuntimeError('登录校验返回的不是 JSON，可能被风控拦截')
    if 'error' not in data:
        raise RuntimeError('登录校验返回格式异常，可能被风控拦截')
    return data['error'] == 0, str(data.get('msg', ''))


def renew(path: Path, account: str) -> Optional[Tuple[bool, str]]:
    """用 LTP0 续期；文件里没有 LTP0 时返回 None，由调用方改用浏览器方式。"""
    cookies = load_cookie_dict(path)
    ltp0 = cookies.get('LTP0')
    if not ltp0:
        return None
    did = cookies.get('dy_did') or cookies.get('acf_did')
    try:
        status, collected = _exchange(ltp0, did)
    except requests.RequestException as e:
        return False, f'斗鱼续期请求失败（网络问题，可稍后再试）：{e}'
    except RuntimeError as e:
        return False, f'斗鱼续期失败：{e}'
    if status != 200:
        return False, f'斗鱼续期失败（HTTP {status}），可稍后再试'
    missing = [key for key in REQUIRED_KEYS if key not in collected]
    if missing:
        return False, '斗鱼长期票据 LTP0 已失效，请点“重新登录”扫码'
    renewed = {**cookies, **{name: value for name, (value, _) in collected.items()}}
    try:
        valid, msg = _probe(renewed)
    except (requests.RequestException, RuntimeError) as e:
        return False, f'斗鱼续期后校验失败，可稍后再试：{e}'
    if not valid:
        return False, f'斗鱼续期后登录无效（{msg}），请点“重新登录”扫码'
    raw: List[dict] = [
        {'name': name, 'expires': expires}
        for name, (_, expires) in collected.items() if expires
    ]
    save_cookie_file('douyu', account, renewed, raw, expiry_keys=EXPIRY_KEYS)
    return True, '斗鱼更新成功（已用 LTP0 换到新的登录 cookie）'


if __name__ == '__main__':
    import sys
    target = Path(sys.argv[1])
    print(renew(target, target.stem.split('_', 1)[-1]))
