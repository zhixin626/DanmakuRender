# -*- coding: utf-8 -*-
"""
browser_login.py
用 Playwright 给 A站 / 抖音 / 斗鱼 登录并保存 cookie，支持一键续期。

每个账号保留一份专属浏览器资料（.login_info/browser/<前缀><账号>/），和平时用的
浏览器一样，关掉再开仍是登录状态：
  - login：弹出浏览器窗口，扫码或输入账号密码；检测到登录成功后自动保存并关闭窗口
  - renew：后台无窗口打开一次网站首页，让网站像平时访问那样刷新登录状态，再保存 cookie；
           若已经不是登录状态，返回失败，需要重新 login

依赖（仅需一次）：
    pip install playwright
    playwright install chromium

命令行（在项目根目录运行）：
    python DMR/Login/browser_login.py acfun 主账号          # 登录
    python DMR/Login/browser_login.py douyin 主账号 --renew  # 续期
"""

from __future__ import annotations

import argparse
import shutil
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from DMR.Login.cookiemgr import LOGIN_INFO_DIR, PLATFORM_PREFIXES, save_cookie_file

LOGIN_TIMEOUT_SECONDS = 300
# 登录项出现后再多等一会儿，让页面把其余 cookie（风控令牌等）也写完
SETTLE_SECONDS = 3
RENEW_WAIT_SECONDS = 6

# Playwright 同一时间只跑一个浏览器任务，避免同一份浏览器资料被同时打开
_LOCK = threading.Lock()


@dataclass
class PlatformSpec:
    name: str                       # 中文名，用于提示
    login_url: str
    home_url: str
    domain: str                     # 只保留这个域名下的 cookie
    login_keys: set                 # 全部出现才算已登录
    expiry_keys: set                # 用来计算过期时间的登录态字段
    pick: Callable[[Dict[str, str]], Dict[str, str]] = field(default=lambda c: c)


def _pick_acfun(cookies: Dict[str, str]) -> Dict[str, str]:
    keep = {'acPasstoken', 'auth_key', '_did', 'acPostHint', 'ac_username', 'ac_userimg'}
    picked = {k: v for k, v in cookies.items() if k in keep}
    return picked or cookies


def _pick_douyin(cookies: Dict[str, str]) -> Dict[str, str]:
    from DMR.Login.douyin_login import _filter_cookies, _sanitize_cookies
    return _sanitize_cookies(_filter_cookies(cookies))


SPECS: Dict[str, PlatformSpec] = {
    'acfun': PlatformSpec(
        name='A站',
        login_url='https://www.acfun.cn/login',
        home_url='https://www.acfun.cn/',
        domain='acfun.cn',
        login_keys={'acPasstoken', 'auth_key'},
        expiry_keys={'acPasstoken', 'auth_key', '_did'},
        pick=_pick_acfun,
    ),
    'douyin': PlatformSpec(
        name='抖音',
        login_url='https://www.douyin.com/',
        home_url='https://www.douyin.com/',
        domain='douyin.com',
        login_keys={'sessionid'},
        expiry_keys={'sid_guard', 'sessionid', 'sid_tt'},
        pick=_pick_douyin,
    ),
    'douyu': PlatformSpec(
        name='斗鱼',
        login_url='https://passport.douyu.com/index/login',
        home_url='https://www.douyu.com/',
        domain='douyu.com',
        login_keys={'acf_uid'},
        expiry_keys={'acf_auth', 'acf_uid', 'acf_stk'},
    ),
}


def profile_dir(platform: str, account: str) -> Path:
    return LOGIN_INFO_DIR / 'browser' / f'{PLATFORM_PREFIXES[platform]}{account}'


def has_profile(platform: str, account: str) -> bool:
    return profile_dir(platform, account).is_dir()


def _spec(platform: str) -> PlatformSpec:
    if platform not in SPECS:
        raise ValueError(f'不支持的平台: {platform}')
    return SPECS[platform]


def _read_cookies(context, spec: PlatformSpec) -> Tuple[Dict[str, str], List[dict]]:
    raw = [c for c in context.cookies() if spec.domain in c.get('domain', '')]
    return {c['name']: c['value'] for c in raw}, raw


def _logged_in(cookies: Dict[str, str], spec: PlatformSpec) -> bool:
    return all(cookies.get(key) for key in spec.login_keys)


def _douyin_ms_token(page, cookies: Dict[str, str], seen_urls: List[str]) -> Optional[str]:
    """msToken 有时不在 cookie 存储里：再从请求 URL 和 document.cookie 找。"""
    from DMR.Login.douyin_login import _extract_ms_token_from_text, _parse_cookie_header
    if cookies.get('msToken'):
        return cookies['msToken']
    for url in reversed(seen_urls):
        token = _extract_ms_token_from_text(url)
        if token:
            return token
    try:
        doc_cookie = page.evaluate("() => document.cookie || ''")
        return _parse_cookie_header(doc_cookie).get('msToken') or _extract_ms_token_from_text(doc_cookie)
    except Exception:
        return None


def _save(platform: str, account: str, spec: PlatformSpec, cookies, raw) -> Path:
    picked = spec.pick(cookies)
    return save_cookie_file(platform, account, picked, raw, expiry_keys=spec.expiry_keys)


def _open(p, platform: str, account: str, headless: bool):
    directory = profile_dir(platform, account)
    directory.mkdir(parents=True, exist_ok=True)
    return p.chromium.launch_persistent_context(
        str(directory),
        headless=headless,
        viewport={'width': 1280, 'height': 800},
        args=['--disable-blink-features=AutomationControlled'],
    )


def _sync_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError('Playwright 未安装，请先执行 pip install playwright 和 playwright install chromium')
    return sync_playwright()


def login(platform: str, account: str, timeout: int = LOGIN_TIMEOUT_SECONDS) -> Tuple[bool, str]:
    """弹出浏览器等用户登录，登录成功后自动保存 cookie 并关闭窗口。"""
    spec = _spec(platform)
    if not _LOCK.acquire(blocking=False):
        return False, '已有一个登录/更新任务在进行，请稍后再试'
    try:
        with _sync_playwright() as p:
            context = _open(p, platform, account, headless=False)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                seen_urls: List[str] = []
                page.on('request', lambda request: seen_urls.append(request.url))
                try:
                    page.goto(spec.login_url, wait_until='domcontentloaded', timeout=30_000)
                except Exception:
                    pass
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    try:
                        cookies, _ = _read_cookies(context, spec)
                    except Exception:
                        return False, '浏览器窗口被关闭，登录已取消'
                    if _logged_in(cookies, spec):
                        break
                    time.sleep(2)
                else:
                    return False, f'{timeout // 60} 分钟内没有检测到登录，已取消'
                time.sleep(SETTLE_SECONDS)
                cookies, raw = _read_cookies(context, spec)
                if platform == 'douyin' and not cookies.get('msToken'):
                    token = _douyin_ms_token(page, cookies, seen_urls)
                    if token:
                        cookies['msToken'] = token
                output = _save(platform, account, spec, cookies, raw)
            finally:
                try:
                    context.close()
                except Exception:
                    pass
        return True, f'{spec.name}登录成功，已保存到 {output}'
    except Exception as e:
        return False, f'{spec.name}登录失败：{e}'
    finally:
        _LOCK.release()


def renew(platform: str, account: str, headless: bool = True) -> Tuple[bool, str]:
    """后台打开一次网站首页刷新登录状态，再保存 cookie。"""
    spec = _spec(platform)
    if not has_profile(platform, account):
        return False, '这个账号还没有保存浏览器资料，请先点“登录”扫码一次'
    if not _LOCK.acquire(blocking=False):
        return False, '已有一个登录/更新任务在进行，请稍后再试'
    try:
        with _sync_playwright() as p:
            context = _open(p, platform, account, headless=headless)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                seen_urls: List[str] = []
                page.on('request', lambda request: seen_urls.append(request.url))
                try:
                    page.goto(spec.home_url, wait_until='domcontentloaded', timeout=30_000)
                except Exception:
                    pass
                time.sleep(RENEW_WAIT_SECONDS)
                cookies, raw = _read_cookies(context, spec)
                if not _logged_in(cookies, spec):
                    return False, f'{spec.name}登录已失效，请点“登录”重新扫码'
                if platform == 'douyin' and not cookies.get('msToken'):
                    token = _douyin_ms_token(page, cookies, seen_urls)
                    if token:
                        cookies['msToken'] = token
                _save(platform, account, spec, cookies, raw)
            finally:
                try:
                    context.close()
                except Exception:
                    pass
        return True, f'{spec.name}更新成功'
    except Exception as e:
        return False, f'{spec.name}更新失败：{e}'
    finally:
        _LOCK.release()


def remove_profile(platform: str, account: str) -> None:
    shutil.rmtree(profile_dir(platform, account), ignore_errors=True)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description='登录 A站/抖音/斗鱼 并保存 cookie，或续期已登录的账号')
    parser.add_argument('platform', choices=sorted(SPECS))
    parser.add_argument('account', help='账号名，决定文件名 .login_info/<平台前缀><账号名>.json')
    parser.add_argument('--renew', action='store_true', help='续期已登录的账号（后台运行，不弹窗）')
    args = parser.parse_args(argv)
    if args.renew:
        ok, message = renew(args.platform, args.account)
    else:
        print(f'[INFO] 正在打开浏览器，请在 {LOGIN_TIMEOUT_SECONDS // 60} 分钟内完成登录，成功后会自动保存并关闭窗口。')
        ok, message = login(args.platform, args.account)
    print(('[OK]  ' if ok else '[ERROR] ') + message)
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
