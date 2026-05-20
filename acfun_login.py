# -*- coding: utf-8 -*-
"""
acfun_login.py
用 Playwright 打开浏览器，手动扫码或登录 AcFun 后自动提取并保存 Cookie。
保存的 cookie 供 DMR 上传时直接使用，不会再触发登录 API，不影响手机/网页端登录状态。

依赖安装（仅需一次）：
    pip install playwright
    playwright install chromium

用法示例：
    python acfun_login.py                              # 默认输出到 .login_info/acfun_cookies.json
    python acfun_login.py --account 佐佐酱小号         # 指定账号名
    python acfun_login.py --output .login_info/acfun_cookies.json
    python acfun_login.py --browser firefox
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

LOGIN_URL = 'https://www.acfun.cn/login'

REQUIRED_KEYS  = {'acPasstoken', 'auth_key', '_did'}
SUGGESTED_KEYS = REQUIRED_KEYS | {'acPostHint', 'ac_username', 'ac_userimg'}


# ---------------------------------------------------------------------------
# 参数解析
# ---------------------------------------------------------------------------

def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='打开浏览器，手动登录 AcFun 后提取并保存 Cookie',
    )
    parser.add_argument('--account', default='acfun',
                        help='账号名，决定 cookie 文件名（默认 acfun）')
    parser.add_argument('--output', type=Path, default=None,
                        help='cookie 输出路径，默认 .login_info/{account}_cookies.json')
    parser.add_argument('--browser', choices=['chromium', 'firefox', 'webkit'],
                        default='chromium', help='浏览器引擎（默认 chromium）')
    parser.add_argument('--headless', action='store_true',
                        help='无头模式（不推荐，无法手动登录）')
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

async def capture_cookies(args: argparse.Namespace) -> int:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print(
            '\n[ERROR] Playwright 未安装，请先执行：\n'
            '    pip install playwright\n'
            '    playwright install chromium\n',
            file=sys.stderr,
        )
        return 1

    output: Path = args.output or Path(f'.login_info/{args.account}.json')

    async with async_playwright() as p:
        browser_factory = getattr(p, args.browser)
        browser = await browser_factory.launch(headless=args.headless)
        context = await browser.new_context()
        page = await context.new_page()

        print('\n[INFO] 浏览器已启动，正在打开 AcFun 登录页面...')
        try:
            await page.goto(LOGIN_URL, wait_until='domcontentloaded', timeout=30_000)
        except Exception:
            pass

        print('[INFO] 请在浏览器中完成登录（扫码或账号密码均可）。')
        print('[INFO] 登录成功后，回到本终端按 Enter 键提取 Cookie...\n')

        # 等待用户按 Enter（单独 print 提示避免 Windows 下 input() 提示不显示的问题）
        await asyncio.to_thread(input)

        # 提取 acfun.cn 下所有 cookie
        storage = await context.storage_state()
        cookies: Dict[str, str] = {
            c['name']: c['value']
            for c in storage['cookies']
            if 'acfun.cn' in c.get('domain', '')
        }

        await context.close()
        await browser.close()

    if not cookies:
        print('[ERROR] 未获取到任何 cookie，请确认已完成登录后重试。')
        return 1

    # 验证关键字段
    missing = REQUIRED_KEYS - cookies.keys()
    if missing:
        print(f'[WARN] 以下关键 cookie 未获取到：{", ".join(sorted(missing))}')
        print('[WARN] 建议确认已完整登录后重新运行。')

    # 过滤只保留需要的字段
    picked = {k: v for k, v in cookies.items() if k in SUGGESTED_KEYS}
    if not picked:
        picked = cookies

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(picked, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )

    print(f'[OK]  已保存 {len(picked)} 个 cookie 到：{output.resolve()}')
    print(f'      保存字段：{", ".join(sorted(picked.keys()))}')
    if not missing:
        print('[OK]  所有关键字段均存在，cookie 可直接使用。')
    print('[INFO] 后续 DMR 上传时将直接复用此 cookie，不会触发登录，不影响手机/网页端。')
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    return asyncio.run(capture_cookies(args))


if __name__ == '__main__':
    raise SystemExit(main())
