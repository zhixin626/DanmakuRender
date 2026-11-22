"""
douyin_login.py
打开浏览器登录抖音，检测到登录成功后自动保存 Cookie 并关闭窗口。
每个账号保留一份浏览器资料，之后可以在网页端一键更新（或加 --renew）。
这里还放着抖音 cookie 的清洗/筛选工具函数，供 browser_login.py 使用。

依赖安装（仅需一次）：
    pip install playwright
    playwright install chromium

用法示例（在项目根目录下运行）：
    python DMR/Login/douyin_login.py 账号名           # 保存到 .login_info/抖音_账号名.json
    python DMR/Login/douyin_login.py 账号名 --renew   # 后台续期，不弹窗
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Dict, Mapping, Optional
from urllib.parse import unquote

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ---------------------------------------------------------------------------
# 内联自 cookie_utils.py
# ---------------------------------------------------------------------------

# RFC6265 token 分隔符与空白字符
_INVALID_COOKIE_NAME_CHARS = set('()<>@,;:\\"/[]?={} \t\r\n')


def _is_valid_cookie_name(name: str) -> bool:
    if not name or not isinstance(name, str):
        return False
    if any(ord(ch) < 33 or ord(ch) > 126 for ch in name):
        return False
    if any(ch in _INVALID_COOKIE_NAME_CHARS for ch in name):
        return False
    return True


def _sanitize_cookies(cookies: Mapping[Any, Any]) -> Dict[str, str]:
    sanitized: Dict[str, str] = {}
    for raw_key, raw_value in (cookies or {}).items():
        if not isinstance(raw_key, str):
            continue
        key = raw_key.strip()
        if not _is_valid_cookie_name(key):
            continue
        value = "" if raw_value is None else str(raw_value).strip()
        sanitized[key] = value
    return sanitized


def _parse_cookie_header(cookie_header: str) -> Dict[str, str]:
    if not cookie_header:
        return {}
    parsed: Dict[str, str] = {}
    for item in cookie_header.split(";"):
        item = item.strip()
        if not item or "=" not in item:
            continue
        key, value = item.split("=", 1)
        key = key.strip()
        if not _is_valid_cookie_name(key):
            continue
        parsed[key] = value.strip()
    return parsed


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

REQUIRED_KEYS = {"msToken", "ttwid", "odin_tt", "passport_csrf_token"}
SUGGESTED_KEYS = REQUIRED_KEYS | {"sid_guard", "sessionid", "sid_tt"}
# 真正代表登录会话有效期的字段；msToken/ttwid 等是风控令牌，可能比登录会话短命得多，不能拿来算过期时间
SESSION_KEYS = {"sid_guard", "sessionid", "sid_tt"}
DEFAULT_AUXILIARY_KEYS = {
    "_waftokenid",
    "s_v_web_id",
    "__ac_nonce",
    "__ac_signature",
    "UIFID",
    "UIFID_TEMP",
    "d_ticket",
    "x-web-secsdk-uid",
    "__security_server_data_status",
}
DEFAULT_AUXILIARY_PREFIXES = (
    "__security_mc_",
    "bd_ticket_guard_",
    "_bd_ticket_crypt_",
)


# ---------------------------------------------------------------------------
# msToken 提取
# ---------------------------------------------------------------------------

def _extract_ms_token_from_text(text: str) -> Optional[str]:
    if not text:
        return None
    patterns = [
        r"(?:^|[;,&\s\"'])msToken=([^;,&\s\"']+)",
        r'"msToken"\s*:\s*"([^"]+)"',
        r"'msToken'\s*:\s*'([^']+)'",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        token = (match.group(1) or "").strip()
        if token:
            return unquote(token)
    return None


# ---------------------------------------------------------------------------
# Cookie 过滤
# ---------------------------------------------------------------------------

def _filter_cookies(cookies: Dict[str, str]) -> Dict[str, str]:
    cookies = _sanitize_cookies(cookies)
    picked: Dict[str, str] = {}
    for key, value in cookies.items():
        if key in SUGGESTED_KEYS or key in DEFAULT_AUXILIARY_KEYS:
            picked[key] = value
            continue
        if any(key.startswith(prefix) for prefix in DEFAULT_AUXILIARY_PREFIXES):
            picked[key] = value
    # 若过滤后为空（登录信息极少），退回原始全集
    return picked if picked else cookies


# ---------------------------------------------------------------------------
# 命令行：登录/续期交给 browser_login
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from DMR.Login.browser_login import main
    raise SystemExit(main(["douyin", *sys.argv[1:]]))
