# -*- coding: utf-8 -*-
"""
直播弹幕实时监听(调试/预览用)

用法:
    python run_danmu.py <直播间URL> [选项]

示例:
    python run_danmu.py https://live.bilibili.com/22230707
    python run_danmu.py https://live.douyin.com/zcw199608
    python run_danmu.py https://www.douyu.com/3125893 --only danmaku,gift,sc
    python run_danmu.py <url> --grep 关键词        # 只看含关键词的弹幕
    python run_danmu.py <url> --all                # 连进场/未知消息也显示

选项:
    --cookie 路径    指定 cookie(默认按平台自动找 .login_info/ 下最新的)
    --only 类型,..   只显示这些类型: danmaku emoticon gift sc member entry other
    --grep 词        只显示 内容/昵称 含该词的
    --all            显示全部类型(含 entry 进场、other 未知)
    --no-color       关闭彩色
    --debug          打印未识别消息的原始内容
"""
import argparse
import asyncio
import glob
import logging
import os
import sys
from datetime import datetime

# 禁用代理,防止干扰弹幕 WS 的 SSL 握手
os.environ['http_proxy'] = ''
os.environ['https_proxy'] = ''
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from DMR.LiveAPI.danmaku import DanmakuClient
from DMR.utils import SimpleDanmaku

logging.basicConfig(level=logging.WARNING, format='%(asctime)s %(levelname)s %(message)s')

C = dict(reset="\033[0m", dim="\033[2m", red="\033[31m", green="\033[32m",
         yellow="\033[33m", blue="\033[34m", magenta="\033[35m", cyan="\033[36m", gray="\033[90m")
USE_COLOR = True
def c(s, name):
    return f"{C[name]}{s}{C['reset']}" if USE_COLOR else str(s)

PLATFORMS = {"bilibili.com": "bilibili", "douyin.com": "douyin",
             "douyu.com": "douyu", "huya.com": "huya"}

def detect_platform(url):
    for k, v in PLATFORMS.items():
        if k in url:
            return v
    return ""

def auto_cookie(platform):
    pat = {"bilibili": ".login_info/b站_*.json", "douyin": ".login_info/抖音_*.json"}.get(platform)
    if not pat:
        return None
    cands = sorted(glob.glob(pat), key=os.path.getmtime, reverse=True)
    return cands[0] if cands else None

def to_danmaku(dm):
    """把队列里的 dict 统一转成 SimpleDanmaku"""
    if isinstance(dm, SimpleDanmaku):
        return dm
    if isinstance(dm, dict):
        return SimpleDanmaku(
            dtype=dm.get('msg_type') or dm.get('dtype') or 'other',
            uname=dm.get('name') or dm.get('uname') or '',
            content=dm.get('content', ''),
            timestamp=dm.get('timestamp'),
            color=dm.get('color', 'ffffff'),
            uid=dm.get('uid'),
        )
    return None

def format_line(dm):
    t = datetime.fromtimestamp(dm.timestamp or datetime.now().timestamp()).strftime('%H:%M:%S')
    ts = c(t, 'gray')
    dt = dm.dtype
    name = getattr(dm, 'uname', '') or ''
    text = getattr(dm, 'text', None)
    content = getattr(dm, 'content', '') or ''
    if dt in ('danmaku', 'emoticon'):
        return f"{ts} {c(name, 'cyan')}{who_uid(dm)}: {text or content}"
    if dt == 'gift':
        return f"{ts} {c('🎁礼物', 'blue')} {who_uid(dm)} {text or content}"
    if dt in ('superchat', 'sc'):
        price = getattr(dm, 'price', ''); unit = getattr(dm, 'price_unit', '')
        return f"{ts} {c('💎SC', 'yellow')} {c(name, 'yellow')}{who_uid(dm)} {price}{unit}: {content or text}"
    if dt == 'member':
        return f"{ts} {c('⚓舰长', 'magenta')} {who_uid(dm)} {text or content}"
    if dt in ('entry', 'enter'):
        return f"{ts} {c('→ ' + (text or name + ' 进入直播间'), 'gray')}{who_uid(dm)}"
    if dt == 'guard_buy':
        return f"{ts} {c('⚓上舰', 'red')} {who_uid(dm)} {text or content}"
    return f"{ts} {c('[' + str(dt) + ']', 'gray')} {c(name,'cyan')}{who_uid(dm)} {text or content}"

def who_uid(dm):
    """返回 uid 标注(灰色,如 (uid:12345));无 uid 返回空串。方便复制到 uid_lists 测 VIP。"""
    uid = getattr(dm, 'uid', None)
    return c(f"(uid:{uid})", 'gray') if uid not in (None, '', 0) else ''


async def listen(url, kwargs, show_types, grep, debug):
    q = asyncio.Queue()
    client = DanmakuClient(url, q, **kwargs)
    task = asyncio.create_task(client.start())
    print(c(f"[*] 已连接 {url},监听中(Ctrl+C 停止)\n", 'green'))
    try:
        while True:
            raw = await q.get()
            dm = to_danmaku(raw)
            if dm is None:
                continue
            if debug and dm.dtype == 'other':
                print(c(f"[RAW] {raw}", 'gray'))
            if show_types is not None and dm.dtype not in show_types:
                continue
            if grep and grep not in (getattr(dm, 'content', '') or '') and grep not in (getattr(dm, 'uname', '') or '') \
                    and grep not in (getattr(dm, 'text', '') or ''):
                continue
            print(format_line(dm))
    except asyncio.CancelledError:
        pass
    finally:
        print(c("\n[*] 关闭连接...", 'green'))
        try:
            await client.stop()
        except Exception:
            pass
        task.cancel()


def main():
    ap = argparse.ArgumentParser(add_help=True, description="直播弹幕实时监听",
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("url", nargs="?", help="直播间 URL")
    ap.add_argument("--cookie", help="cookie 文件(默认按平台自动找)")
    ap.add_argument("--only", help="只显示的类型,逗号分隔: danmaku,emoticon,gift,sc,member,entry,other")
    ap.add_argument("--grep", help="只显示 内容/昵称 含该关键词的")
    ap.add_argument("--all", action="store_true", help="显示全部类型(含 entry/other)")
    ap.add_argument("--no-color", action="store_true", help="关闭彩色")
    ap.add_argument("--debug", action="store_true", help="打印未识别消息原始内容")
    args = ap.parse_args()

    if not args.url:
        ap.print_help()
        sys.exit(0)

    global USE_COLOR
    USE_COLOR = not args.no_color
    if USE_COLOR and sys.platform == 'win32':
        try:
            import colorama; colorama.just_fix_windows_console()
        except Exception:
            os.system("")  # 启用 Win10+ 终端的 ANSI

    platform = detect_platform(args.url)
    if not platform:
        print(c(f"[!] 无法识别平台(支持 bilibili/douyin/douyu/huya): {args.url}", 'red'))
        sys.exit(1)

    # 组装 cookie 参数(只传该平台需要的)
    kwargs = {}
    cookie = args.cookie or auto_cookie(platform)
    if cookie and not os.path.exists(cookie):
        print(c(f"[!] cookie 文件不存在: {cookie}(将用游客身份)", 'yellow')); cookie = None
    if platform == "bilibili" and cookie:
        kwargs["bilibili_dm_cookie_path"] = cookie
    elif platform == "douyin" and cookie:
        kwargs["douyin_dm_cookies"] = cookie

    # 显示哪些类型
    if args.only:
        show_types = set(s.strip() for s in args.only.split(",") if s.strip())
        show_types |= {"superchat"} if "sc" in show_types else set()
    elif args.all:
        show_types = None  # 全部
    else:
        show_types = {"danmaku", "emoticon", "gift", "superchat", "sc", "member", "guard_buy"}

    print(c(f"[*] 平台={platform} cookie={cookie or '游客'}", 'gray'))

    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(listen(args.url, kwargs, show_types, args.grep, args.debug))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
