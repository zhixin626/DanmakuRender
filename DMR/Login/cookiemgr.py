import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# .login_info 里文件名前缀 <-> 平台的对应关系，登录脚本和网页端共用这一份
PLATFORM_PREFIXES = {
    'bilibili': 'b站_',
    'acfun': 'A站_',
    'douyin': '抖音_',
    'douyu': '斗鱼_',
}

LOGIN_INFO_DIR = Path('.login_info')

# 剩余不到这么久就在网页端标成“即将过期”（斗鱼 cookie 本身只有约 6 天有效期）
EXPIRING_SOON_SECONDS = 2 * 24 * 3600


def save_cookie_file(platform: str, account: str, cookies: Dict[str, str],
                      raw_playwright_cookies: Optional[List[Dict[str, Any]]] = None,
                      expiry_keys: Optional[set] = None) -> Path:
    """
    给 browser_login.py 用：把过滤后的 cookie 字典存成统一的带壳格式。
    expires_at 取 raw_playwright_cookies 里的过期时间戳算出来。
    只看 expiry_keys 里指定的那几个「登录态」字段（不传就退回看 cookies 里的全部字段）——
    像抖音的 bd_ticket_guard_*/__security_mc_* 这类风控签名 cookie 本身就是短命的、
    会被页面 JS 频繁自动刷新，跟登录会话是否有效没关系，混进来算 min 会把过期时间拉得离谱地近。
    全是会话级(没有固定过期时间)或一个都没命中则为 None。
    """
    prefix = PLATFORM_PREFIXES.get(platform)
    if not prefix:
        raise ValueError(f'未知平台: {platform}')

    expires_at = None
    if raw_playwright_cookies:
        names = expiry_keys if expiry_keys is not None else cookies.keys()
        candidates = [
            c.get('expires') for c in raw_playwright_cookies
            if c.get('name') in names and c.get('name') in cookies and c.get('expires') and c.get('expires') > 0
        ]
        if candidates:
            expires_at = int(min(candidates))

    output = LOGIN_INFO_DIR / f'{prefix}{account}.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        'platform': platform,
        'account': account,
        'cookies': cookies,
        'expires_at': expires_at,
        'updated_at': int(time.time()),
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    return output


def load_cookie_dict(path) -> Dict[str, str]:
    """
    给 acfun.py / 抖音弹幕监听 用：读取 cookie 文件，兼容新的带壳格式和老的扁平格式。
    """
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    if isinstance(data, dict) and isinstance(data.get('cookies'), dict):
        return data['cookies']
    return data


# 网页端发起的浏览器登录在后台线程里跑（要等用户扫码），这里记录每个账号的进度
_LOGIN_JOBS: Dict[str, Dict[str, str]] = {}


class CookieManager:
    """扫描 .login_info/，统一展示各平台 cookie 的过期状态，供网页端使用。"""

    def __init__(self, login_info_dir: Optional[str] = None):
        self.login_info_dir = Path(login_info_dir) if login_info_dir else LOGIN_INFO_DIR

    def _identify(self, filename: str) -> Tuple[str, str]:
        stem = filename[:-len('.json')] if filename.endswith('.json') else filename
        for platform, prefix in PLATFORM_PREFIXES.items():
            if filename.startswith(prefix):
                return platform, stem[len(prefix):]
        return 'unknown', stem

    def _status(self, expires_at: Optional[int]) -> str:
        if expires_at is None:
            return 'unknown'
        remaining = expires_at - time.time()
        if remaining <= 0:
            return 'expired'
        if remaining <= EXPIRING_SOON_SECONDS:
            return 'expiring_soon'
        return 'valid'

    def _read_bilibili_expires(self, path: Path) -> Optional[int]:
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            cookies = data.get('cookie_info', {}).get('cookies', [])
            candidates = [c['expires'] for c in cookies if c.get('expires')]
            return int(min(candidates)) if candidates else None
        except Exception:
            return None

    def list_cookies(self) -> List[Dict[str, Any]]:
        results = []
        if not self.login_info_dir.exists():
            return results

        for path in sorted(self.login_info_dir.glob('*.json')):
            platform, account = self._identify(path.name)
            expires_at = None
            updated_at = None

            try:
                if platform == 'bilibili':
                    expires_at = self._read_bilibili_expires(path)
                else:
                    with open(path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    if isinstance(data, dict):
                        expires_at = data.get('expires_at')
                        updated_at = data.get('updated_at')
                        if platform == 'unknown' and data.get('platform') in PLATFORM_PREFIXES:
                            platform = data['platform']
                            account = data.get('account', account)
            except Exception:
                pass

            if updated_at is None:
                try:
                    updated_at = int(path.stat().st_mtime)
                except OSError:
                    updated_at = None

            has_profile = (self.login_info_dir / 'browser' / path.stem).is_dir()
            results.append({
                'filename': path.name,
                'platform': platform,
                'account': account,
                # A站/抖音/斗鱼：有浏览器资料才能一键更新，否则要先登录
                'browser_login': platform in ('acfun', 'douyin', 'douyu'),
                'has_profile': has_profile,
                'expires_at': expires_at,
                'updated_at': updated_at,
                'status': self._status(expires_at),
            })

        return results

    def renew(self, filename: str) -> Tuple[bool, str]:
        platform, account = self._identify(filename)
        path = self.login_info_dir / filename

        if platform == 'douyu' and path.exists():
            # 有 LTP0 就直接换票，不用开浏览器，而且能真正延长有效期
            from DMR.Login import douyu_auth
            result = douyu_auth.renew(path, account)
            if result is not None:
                return result

        if platform != 'bilibili':
            from DMR.Login import browser_login
            if platform not in browser_login.SPECS:
                return False, f'无法识别 {filename} 属于哪个平台'
            return browser_login.renew(platform, account)

        if not path.exists():
            return False, f'找不到 cookie 文件: {path}'

        from DMR.utils.logintools import bili_islogin
        ok = bili_islogin(str(path))
        return ok, ('续期成功' if ok else '续期失败，cookie 可能已彻底失效，需要重新登录')

    @staticmethod
    def _bili_login(account: str) -> Tuple[bool, str]:
        """B站沿用 biliup-rs 登录（登录信息里带刷新令牌，之后才能一键更新）：
        在电脑上弹出一个命令行窗口让用户扫码。"""
        import os
        import subprocess
        from DMR.utils.logintools import bili_islogin
        from DMR.utils.toolsmgr import ToolsList
        path = LOGIN_INFO_DIR / f"{PLATFORM_PREFIXES['bilibili']}{account}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        command = [ToolsList.get('biliup'), '-u', str(path), 'login']
        if os.name != 'nt':
            return False, '只有 Windows 能从网页端弹出登录窗口，请在终端运行：' + ' '.join(command)
        proc = subprocess.Popen(command, creationflags=subprocess.CREATE_NEW_CONSOLE)
        try:
            proc.wait(timeout=600)
        except subprocess.TimeoutExpired:
            proc.kill()
            return False, '10 分钟内没有完成登录，已取消'
        if path.exists() and bili_islogin(str(path)):
            return True, f'B站登录成功，已保存到 {path}'
        return False, 'B站登录没有完成（窗口被关闭或扫码失败）'

    @staticmethod
    def start_login(platform: str, account: str) -> Tuple[bool, str]:
        """在后台线程打开登录窗口让用户扫码；进度用 login_status 查询。"""
        from DMR.Login import browser_login
        if platform != 'bilibili' and platform not in browser_login.SPECS:
            return False, f'不支持网页端登录的平台: {platform}'
        account = (account or '').strip()
        if not account or any(ch in account for ch in '\\/:*?"<>|'):
            return False, '账号名不能为空，也不能包含 \\ / : * ? " < > |'
        filename = f'{PLATFORM_PREFIXES[platform]}{account}.json'
        if _LOGIN_JOBS.get(filename, {}).get('state') == 'running':
            return False, '这个账号正在登录中'
        if platform == 'bilibili':
            hint = '已在电脑上打开一个命令行窗口：用方向键选“扫码登录”回车，再用B站手机App扫码（10 分钟内）'
        else:
            hint = '已在电脑上打开浏览器，请在 5 分钟内扫码登录，成功后窗口会自动关闭'
        _LOGIN_JOBS[filename] = {'state': 'running', 'message': hint}

        def run():
            if platform == 'bilibili':
                ok, message = CookieManager._bili_login(account)
            else:
                ok, message = browser_login.login(platform, account)
            _LOGIN_JOBS[filename] = {'state': 'ok' if ok else 'error', 'message': message}

        threading.Thread(target=run, daemon=True).start()
        return True, filename

    @staticmethod
    def login_status(filename: str) -> Dict[str, str]:
        return _LOGIN_JOBS.get(filename, {'state': 'none', 'message': ''})
