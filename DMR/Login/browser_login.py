# -*- coding: utf-8 -*-
"""
browser_login.py
用 Playwright 给 A站 / 抖音 / 斗鱼 登录并保存 cookie，支持一键续期。

每个账号保留一份专属浏览器资料（.login_info/browser/<前缀><账号>/），和平时用的
浏览器一样，关掉再开仍是登录状态：
  - login：用全新的浏览器资料弹出窗口，扫码或输入账号密码，登录后自己关掉窗口，程序再保存 cookie
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
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
if __name__ == '__main__' and 'DMR' not in sys.modules:
    # 单独运行时只用到 DMR.Login：跳过 DMR/__init__.py，不加载整个程序，启动更快
    import types
    _package = types.ModuleType('DMR')
    _package.__path__ = [str(_PROJECT_ROOT / 'DMR')]
    sys.modules['DMR'] = _package

from DMR.Login.cookiemgr import LOGIN_INFO_DIR, PLATFORM_PREFIXES, save_cookie_file

LOGIN_TIMEOUT_SECONDS = 600
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


def _open(p, platform: str, account: str, headless: bool, offscreen: bool = False):
    directory = profile_dir(platform, account)
    directory.mkdir(parents=True, exist_ok=True)
    args = ['--disable-blink-features=AutomationControlled']
    if offscreen:
        # 有界面但放到屏幕外：比无界面模式更像真人访问，不容易被风控识别
        args.append('--window-position=-32000,-32000')
    return p.chromium.launch_persistent_context(
        str(directory),
        headless=headless,
        viewport={'width': 1280, 'height': 800},
        args=args,
    )


def _sync_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError('Playwright 未安装，请先执行 pip install playwright 和 playwright install chromium')
    return sync_playwright()


def _login(platform: str, account: str, timeout: int = LOGIN_TIMEOUT_SECONDS) -> Tuple[bool, str]:
    """用全新的浏览器资料打开登录页；用户登录后自己关掉浏览器窗口，再保存 cookie。"""
    spec = _spec(platform)
    if not _LOCK.acquire(blocking=False):
        return False, '已有一个登录/更新任务在进行，请稍后再试'
    try:
        # 重新登录：丢掉旧的浏览器资料，从干净的窗口开始，避免旧登录状态干扰
        remove_profile(platform, account)
        snapshot: Dict[str, str] = {}
        snapshot_raw: List[dict] = []
        seen_urls: List[str] = []
        timed_out = False
        with _sync_playwright() as p:
            context = _open(p, platform, account, headless=False)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                context.on('request', lambda request: seen_urls.append(request.url))
                try:
                    page.goto(spec.login_url, wait_until='domcontentloaded', timeout=30_000)
                except Exception:
                    pass
                deadline = time.monotonic() + timeout
                # 每秒记一份 cookie：浏览器关掉以后就读不到了，会话级 cookie 也不会落盘
                while True:
                    try:
                        snapshot, snapshot_raw = _read_cookies(context, spec)
                    except Exception:
                        break                       # 用户关掉了窗口
                    if not context.pages:
                        break
                    if time.monotonic() >= deadline:
                        timed_out = True
                        break
                    time.sleep(1)
            finally:
                try:
                    context.close()
                except Exception:
                    pass
            # 再用同一份浏览器资料（无窗口）读一次落盘的 cookie，补上最后一秒内写入的
            try:
                disk_context = _open(p, platform, account, headless=True)
                try:
                    disk, disk_raw = _read_cookies(disk_context, spec)
                finally:
                    disk_context.close()
            except Exception:
                disk, disk_raw = {}, []
        cookies = {**snapshot, **disk}
        raw = {c['name']: c for c in snapshot_raw}
        raw.update({c['name']: c for c in disk_raw})
        if not _logged_in(cookies, spec):
            reason = f'{timeout // 60} 分钟内没有完成' if timed_out else '关闭窗口时还没有登录'
            return False, f'{spec.name}登录未完成（{reason}），原来的 cookie 文件没有改动'
        if platform == 'douyin' and not cookies.get('msToken'):
            from DMR.Login.douyin_login import _extract_ms_token_from_text
            for url in reversed(seen_urls):
                token = _extract_ms_token_from_text(url)
                if token:
                    cookies['msToken'] = token
                    break
        output = _save(platform, account, spec, cookies, list(raw.values()))
        note = ''
        if platform == 'douyu' and not cookies.get('LTP0'):
            note = '（没拿到长期票据 LTP0，以后只能靠重新登录续期）'
        return True, f'{spec.name}登录成功，已保存到 {output}{note}'
    except Exception as e:
        return False, f'{spec.name}登录失败：{e}'
    finally:
        _LOCK.release()


def _renew(platform: str, account: str) -> Tuple[bool, str]:
    """后台打开一次网站首页刷新登录状态，再保存 cookie。"""
    spec = _spec(platform)
    if not has_profile(platform, account):
        return False, '这个账号还没有保存浏览器资料，请先点“重新登录”扫码一次'
    if not _LOCK.acquire(blocking=False):
        return False, '已有一个登录/更新任务在进行，请稍后再试'
    try:
        with _sync_playwright() as p:
            context = _open(p, platform, account, headless=False, offscreen=True)
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
                    return False, f'{spec.name}登录已失效，请点“重新登录”扫码'
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


def _run_child(platform: str, account: str, renew_only: bool, timeout: int) -> Tuple[bool, str]:
    """Playwright 放到单独的进程里跑：主程序为了弹幕把 asyncio 换成了 Selector 事件循环，
    它在 Windows 上开不了子进程，Playwright 在主进程里会直接报 NotImplementedError。"""
    _spec(platform)
    command = [sys.executable, str(Path(__file__).resolve()), platform, account, '--json']
    if renew_only:
        command.append('--renew')
    if not _LOCK.acquire(blocking=False):
        return False, '已有一个登录/更新任务在进行，请稍后再试'
    try:
        proc = subprocess.run(
            command, cwd=str(Path.cwd()), capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=timeout,
            env={**os.environ, 'PYTHONIOENCODING': 'utf-8'},
        )
    except subprocess.TimeoutExpired:
        return False, '登录/更新超时，已取消'
    finally:
        _LOCK.release()
    for line in reversed((proc.stdout or '').splitlines()):
        if line.startswith('{'):
            try:
                result = json.loads(line)
                return bool(result.get('ok')), str(result.get('message', ''))
            except ValueError:
                break
    detail = (proc.stderr or proc.stdout or '').strip().splitlines()
    return False, '登录/更新进程异常退出：' + (detail[-1] if detail else f'返回码 {proc.returncode}')


def login(platform: str, account: str) -> Tuple[bool, str]:
    return _run_child(platform, account, False, LOGIN_TIMEOUT_SECONDS + 60)


def renew(platform: str, account: str) -> Tuple[bool, str]:
    if not has_profile(platform, account):
        return False, '这个账号还没有保存浏览器资料，请先点“重新登录”扫码一次'
    return _run_child(platform, account, True, 120)


def remove_profile(platform: str, account: str) -> None:
    shutil.rmtree(profile_dir(platform, account), ignore_errors=True)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description='登录 A站/抖音/斗鱼 并保存 cookie，或续期已登录的账号')
    parser.add_argument('platform', choices=sorted(SPECS))
    parser.add_argument('account', help='账号名，决定文件名 .login_info/<平台前缀><账号名>.json')
    parser.add_argument('--renew', action='store_true', help='续期已登录的账号（后台运行，不弹窗）')
    parser.add_argument('--json', action='store_true', help=argparse.SUPPRESS)  # 主程序调用时用
    args = parser.parse_args(argv)
    if args.renew:
        ok, message = _renew(args.platform, args.account)
    else:
        if not args.json:
            print(f'[INFO] 正在打开浏览器，请在 {LOGIN_TIMEOUT_SECONDS // 60} 分钟内完成登录，登录后关掉浏览器窗口即可保存。')
        ok, message = _login(args.platform, args.account)
    if args.json:
        print(json.dumps({'ok': ok, 'message': message}))
    else:
        print(('[OK]  ' if ok else '[ERROR] ') + message)
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
