import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# .login_info 里文件名前缀 <-> 平台的对应关系，登录脚本和网页端共用这一份
PLATFORM_PREFIXES = {
    'bilibili': 'b站_',
    'acfun': 'A站_',
    'douyin': '抖音_',
}

LOGIN_INFO_DIR = Path('.login_info')

EXPIRING_SOON_SECONDS = 7 * 24 * 3600


def save_cookie_file(platform: str, account: str, cookies: Dict[str, str],
                      raw_playwright_cookies: Optional[List[Dict[str, Any]]] = None,
                      expiry_keys: Optional[set] = None) -> Path:
    """
    给 acfun_login.py / douyin_login.py 用：把过滤后的 cookie 字典存成统一的带壳格式。
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


class CookieManager:
    """扫描 .login_info/，统一展示三个平台 cookie 的过期状态，供网页端使用。"""

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

            results.append({
                'filename': path.name,
                'platform': platform,
                'account': account,
                'expires_at': expires_at,
                'updated_at': updated_at,
                'status': self._status(expires_at),
            })

        return results

    def renew(self, filename: str) -> Tuple[bool, str]:
        platform, account = self._identify(filename)
        path = self.login_info_dir / filename

        if platform != 'bilibili':
            script = 'acfun_login.py' if platform == 'acfun' else 'douyin_login.py'
            return False, f'{platform} 平台暂不支持网页端自动更新，请手动运行 python DMR/Login/{script} {account}'

        if not path.exists():
            return False, f'找不到 cookie 文件: {path}'

        from DMR.utils.logintools import bili_islogin
        ok = bili_islogin(str(path))
        return ok, ('续期成功' if ok else '续期失败，cookie 可能已彻底失效，需要重新登录')
