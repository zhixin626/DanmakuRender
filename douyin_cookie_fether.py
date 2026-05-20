"""
douyin_cookie_fetcher.py
用 Playwright 打开浏览器，手动登录抖音后自动提取并保存 Cookie。

依赖安装（仅需一次）：
    pip install playwright
    playwright install chromium      # 或 firefox / webkit

用法示例：
    python douyin_cookie_fetcher.py                          # 默认输出 cookies.json
    python douyin_cookie_fetcher.py --output my_cookies.json
    python douyin_cookie_fetcher.py --browser firefox
    python douyin_cookie_fetcher.py --include-all            # 保存所有 cookie，不过滤
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence
from urllib.parse import parse_qs, unquote, urlparse

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

DEFAULT_URL = "https://www.douyin.com/"
DEFAULT_OUTPUT = Path("cookies.json")

REQUIRED_KEYS = {"msToken", "ttwid", "odin_tt", "passport_csrf_token"}
SUGGESTED_KEYS = REQUIRED_KEYS | {"sid_guard", "sessionid", "sid_tt"}
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

PRIMARY_WAIT_UNTIL = "networkidle"
FALLBACK_WAIT_UNTIL = "domcontentloaded"
PRIMARY_TIMEOUT_MS = 300_000
FALLBACK_TIMEOUT_MS = 300_000

# ---------------------------------------------------------------------------
# 参数解析
# ---------------------------------------------------------------------------

def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Launch a browser, guide manual login, then dump Douyin cookies.",
    )
    parser.add_argument(
        "--url",
        default=DEFAULT_URL,
        help=f"Login page to open (default: {DEFAULT_URL})",
    )
    parser.add_argument(
        "--browser",
        choices=["chromium", "firefox", "webkit"],
        default="chromium",
        help="Playwright browser engine (default: chromium)",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run browser headless (not recommended for manual login)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"JSON file to write collected cookies (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--include-all",
        action="store_true",
        help="Store every cookie from douyin.com instead of the recommended subset",
    )
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# 导航辅助
# ---------------------------------------------------------------------------

def _is_timeout_error(exc: Exception) -> bool:
    return exc.__class__.__name__ == "TimeoutError" or "Timeout" in str(exc)


def _is_target_closed_error(exc: Exception) -> bool:
    return (
        exc.__class__.__name__ == "TargetClosedError"
        or "Target page, context or browser has been closed" in str(exc)
    )


async def _goto_with_fallback(page: Any, url: str) -> str:
    """
    先用 networkidle 等待页面稳定；抖音页面持续有后台请求，
    若超时则降级为 domcontentloaded，再超时则直接继续。
    """
    try:
        await page.goto(url, wait_until=PRIMARY_WAIT_UNTIL, timeout=PRIMARY_TIMEOUT_MS)
        return PRIMARY_WAIT_UNTIL
    except Exception as exc:
        if _is_target_closed_error(exc):
            print("[WARN] Browser/page was closed during initial navigation, continuing.")
            return "target_closed"
        if not _is_timeout_error(exc):
            raise
        print(
            f"[WARN] goto(networkidle) timed out after {PRIMARY_TIMEOUT_MS}ms, "
            "falling back to domcontentloaded."
        )
    try:
        await page.goto(url, wait_until=FALLBACK_WAIT_UNTIL, timeout=FALLBACK_TIMEOUT_MS)
        return FALLBACK_WAIT_UNTIL
    except Exception as exc:
        if _is_target_closed_error(exc):
            print("[WARN] Browser/page was closed during fallback navigation, continuing.")
            return "target_closed"
        if _is_timeout_error(exc):
            print(
                f"[WARN] goto(domcontentloaded) also timed out after {FALLBACK_TIMEOUT_MS}ms, "
                "continuing anyway."
            )
            return "timeout"
        raise


async def _wait_for_login_confirmation(page: Any, url: str, input_func: Any = input) -> None:
    """
    导航与等待用户按 Enter 并发执行：
    - 导航任务放到后台，终端不会卡住
    - 用户按 Enter 后，若导航还没完成则直接取消
    """
    nav_task = asyncio.create_task(_goto_with_fallback(page, url))
    # 让 nav_task 至少进入第一个 await 点，避免极端时序下被立即 cancel
    await asyncio.sleep(0)
    await asyncio.to_thread(input_func)

    if not nav_task.done():
        nav_task.cancel()
        try:
            await nav_task
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            print(f"[WARN] Navigation task ended with error after cancel: {exc}")
        return

    try:
        await nav_task
    except Exception as exc:
        print(f"[WARN] Navigation task ended with error: {exc}")


# ---------------------------------------------------------------------------
# msToken 多路提取
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


async def _try_extract_ms_token(
    page: Any,
    cookies: Dict[str, str],
    observed_cookie_headers: List[str],
    observed_mstokens: List[str],
) -> Optional[str]:
    # ① 已在 cookies 里
    existing = cookies.get("msToken")
    if existing:
        return existing

    # ② 从监听的 URL query 参数取最新一条
    for token in reversed(observed_mstokens):
        token = (token or "").strip()
        if token:
            return token

    # ③ 从监听的请求 Cookie 头解析
    for header in reversed(observed_cookie_headers):
        parsed = _parse_cookie_header(header)
        token = (parsed.get("msToken") or "").strip()
        if token:
            return token
        extra = _extract_ms_token_from_text(header)
        if extra:
            return extra

    # ④ 从 document.cookie（JS 执行）
    try:
        doc_cookie = await page.evaluate("() => document.cookie || ''")
        parsed = _parse_cookie_header(doc_cookie)
        token = (parsed.get("msToken") or "").strip()
        if token:
            return token
        extra = _extract_ms_token_from_text(doc_cookie)
        if extra:
            return extra
    except Exception:
        pass

    # ⑤ 从 localStorage / sessionStorage 模糊匹配
    js = """
() => {
  const values = [];
  const pushIf = (v) => {
    if (typeof v === 'string' && v.trim()) values.push(v.trim());
  };
  try {
    for (const key of Object.keys(localStorage || {})) {
      if (key.toLowerCase().includes('mstoken')) pushIf(localStorage.getItem(key));
    }
  } catch (e) {}
  try {
    for (const key of Object.keys(sessionStorage || {})) {
      if (key.toLowerCase().includes('mstoken')) pushIf(sessionStorage.getItem(key));
    }
  } catch (e) {}
  return values;
}
"""
    try:
        candidates = await page.evaluate(js)
        for candidate in candidates or []:
            if not isinstance(candidate, str):
                continue
            text = candidate.strip()
            if not text:
                continue
            parsed = _parse_cookie_header(text)
            if parsed.get("msToken"):
                return parsed["msToken"]
            extra = _extract_ms_token_from_text(text)
            if extra:
                return extra
            # 若是一个干净的 token 字符串（无分隔符）直接当作 msToken
            if len(text) <= 2048 and all(ch not in text for ch in [";", " ", "\n", "\r", "\t"]):
                return text
    except Exception:
        pass

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
# 主流程
# ---------------------------------------------------------------------------

async def capture_cookies(args: argparse.Namespace) -> int:
    try:
        from playwright.async_api import async_playwright  # type: ignore
    except ImportError:
        print(
            "\n[ERROR] Playwright 未安装，请先执行：\n"
            "    pip install playwright\n"
            "    playwright install chromium\n",
            file=sys.stderr,
        )
        return 1

    async with async_playwright() as p:
        browser_factory = getattr(p, args.browser)
        browser = await browser_factory.launch(headless=args.headless)
        context = await browser.new_context()
        page = await context.new_page()

        observed_cookie_headers: List[str] = []
        observed_mstokens: List[str] = []

        def _on_request(request: Any) -> None:
            try:
                headers = request.headers or {}
                cookie_header = headers.get("cookie")
                if cookie_header:
                    observed_cookie_headers.append(cookie_header)
                url = request.url or ""
                query = parse_qs(urlparse(url).query)
                if "msToken" in query and query["msToken"]:
                    observed_mstokens.append((query["msToken"][0] or "").strip())
                token = _extract_ms_token_from_text(url)
                if token:
                    observed_mstokens.append(token)
            except Exception:
                return

        page.on("request", _on_request)

        print("\n[INFO] 浏览器已启动，请在弹出的窗口中完成抖音登录。")
        print("[INFO] 登录成功后回到本终端，按 Enter 键继续...\n")

        await _wait_for_login_confirmation(page, args.url)

        # 从浏览器存储提取 douyin.com 下的所有 cookie
        storage = await context.storage_state()
        cookies: Dict[str, str] = {
            cookie["name"]: cookie["value"]
            for cookie in storage["cookies"]
            if cookie["domain"].endswith("douyin.com")
        }
        cookies = _sanitize_cookies(cookies)

        # 补充 msToken（可能不在 storage_state 里）
        ms_token = await _try_extract_ms_token(
            page, cookies, observed_cookie_headers, observed_mstokens
        )
        if ms_token and not cookies.get("msToken"):
            cookies["msToken"] = ms_token
            print("[INFO] 已从备用来源提取到 msToken。")

        await context.close()
        await browser.close()

    picked = cookies if args.include_all else _filter_cookies(cookies)
    picked = _sanitize_cookies(picked)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(picked, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[INFO] 已保存 {len(picked)} 个 cookie 到：{args.output.resolve()}")

    missing = REQUIRED_KEYS - picked.keys()
    if missing:
        print(f"[WARN] 以下推荐字段未获取到：{', '.join(sorted(missing))}")
        print("[WARN] 建议确认已完整登录后重新运行。")
    else:
        print("[OK]  所有必要字段均已获取，cookie 可直接使用。")

    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    return asyncio.run(capture_cookies(args))


if __name__ == "__main__":
    raise SystemExit(main())