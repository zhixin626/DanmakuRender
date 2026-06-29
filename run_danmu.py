# -*- coding: utf-8 -*-
"""
直播弹幕实时监听(调试/预览用)

用法:
    python run_danmu.py <直播间URL> [选项]

示例:
    python run_danmu.py https://live.bilibili.com/22230707
    python run_danmu.py https://www.douyu.com/3125893 --only member
    python run_danmu.py <url> --all                # 连进场/未识别消息也显示

选项:
    --cookie 路径    指定 cookie(默认按平台自动找 .login_info/ 下最新的)
    --only 类型,..   只显示这些类型: danmaku gift sc member entry other
    --all            额外显示进场(entry)
    --debug          额外显示未识别的 BaseDanmaku(other),并打印非弹幕对象原文
    --dump-raw [文件] 把 gift/member 的原始消息(SEND_GIFT/USER_TOAST_MSG)追加存到
                     文件(默认 raw_gift_member.jsonl),不受 --only/--all 影响,用于排查重复

默认显示: danmaku gift sc member
"""
import argparse
import asyncio
import glob
import json
import logging
import os
import sys
from datetime import datetime

# 禁用代理,防止干扰弹幕 WS 的 SSL 握手
os.environ['http_proxy'] = ''
os.environ['https_proxy'] = ''
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from DMR.LiveAPI.danmaku import DanmakuClient
from DMR.utils import BaseDanmaku

logging.basicConfig(level=logging.WARNING, format='%(asctime)s %(levelname)s %(message)s')

C = dict(reset="\033[0m", red="\033[31m", green="\033[32m", yellow="\033[33m",
         blue="\033[34m", magenta="\033[35m", cyan="\033[36m", gray="\033[90m")
def c(s, name):
    return f"{C[name]}{s}{C['reset']}"

PLATFORMS = {"bilibili.com": "bilibili", "douyin.com": "douyin",
             "douyu.com": "douyu", "huya.com": "huya"}

# 显示类型 -> 包含的 dtype；other 兜底所有未列出的(系统/未识别消息)
CATEGORIES = {
    'danmaku': {'danmaku', 'emoticon'},
    'gift':    {'gift'},
    'sc':      {'superchat'},
    'member':  {'member'},
    'entry':   {'entry'},
    'other':   set(),
}
DEFAULT_CATS = {'danmaku', 'gift', 'sc', 'member'}   # 默认显示；--all 加 entry、--debug 加 other


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


def dm_category(dtype):
    for cat, dtypes in CATEGORIES.items():
        if dtype in dtypes:
            return cat
    return 'other'


def who_uid(dm):
    """uid 标注(灰色,如 (uid:12345));无 uid 返回空串。方便复制去测 VIP 名单。"""
    uid = getattr(dm, 'uid', None)
    return c(f"(uid:{uid})", 'gray') if uid not in (None, '', 0) else ''


def format_line(dm):
    ts = c(datetime.fromtimestamp(dm.timestamp).strftime('%H:%M:%S'), 'gray')
    dt = dm.dtype
    name = getattr(dm, 'uname', '') or ''
    text = getattr(dm, 'text', None)
    content = getattr(dm, 'content', '') or ''
    uid = who_uid(dm)
    if dt in ('danmaku', 'emoticon'):
        return f"{ts} {c(name, 'cyan')}{uid}: {text or content}"
    if dt == 'gift':
        return f"{ts} {c('🎁礼物', 'blue')} {c(name, 'cyan')}{uid} {text or content}"
    if dt == 'superchat':
        price = getattr(dm, 'price', ''); unit = getattr(dm, 'price_unit', '')
        return f"{ts} {c('💎SC', 'yellow')} {c(name, 'yellow')}{uid} {price}{unit}: {content or text}"
    if dt == 'member':
        return f"{ts} {c('⚓舰长', 'magenta')} {c(name, 'cyan')}{uid} {text or content}"
    if dt == 'entry':
        return f"{ts} {c('→ ' + (text or name + ' 进入直播间'), 'gray')}{uid}"
    return f"{ts} {c('[' + str(dt) + ']', 'gray')} {c(name, 'cyan')}{uid} {text or content}"


async def listen(url, kwargs, show_cats, debug, dump_raw=None):
    q = asyncio.Queue()
    client = DanmakuClient(url, q, **kwargs)
    task = asyncio.create_task(client.start())
    print(c(f"[*] 已连接 {url},监听中(Ctrl+C 停止)\n", 'green'))
    try:
        while True:
            raw = await q.get()
            if not isinstance(raw, BaseDanmaku):   # decoder 现统一产出弹幕对象;非对象仅调试时看
                if debug:
                    print(c(f"[RAW] {raw}", 'gray'))
                continue
            # gift/member 的原始消息落盘(不受显示过滤影响),用于排查重复(看 tid/payflow_id)
            if dump_raw and raw.dtype in ('gift', 'member'):
                with open(dump_raw, 'a', encoding='utf-8') as fp:
                    fp.write(json.dumps(getattr(raw, 'raw', None), ensure_ascii=False) + '\n')
            if dm_category(raw.dtype) in show_cats:
                print(format_line(raw))
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
    ap.add_argument("--only", help="只显示的类型,逗号分隔: " + " ".join(CATEGORIES))
    ap.add_argument("--all", action="store_true", help="额外显示进场(entry)")
    ap.add_argument("--debug", action="store_true", help="额外显示未识别的 BaseDanmaku(other),并打印非弹幕对象原文")
    ap.add_argument("--dump-raw", nargs="?", const="raw_gift_member.jsonl", default=None,
                    help="把 gift/member 的原始消息追加存到文件(默认 raw_gift_member.jsonl),用于排查重复")
    args = ap.parse_args()

    if not args.url:
        ap.print_help()
        sys.exit(0)

    # 选择要显示的类型；--only 传了不存在的类型(如 memeber)直接报错退出
    if args.only:
        cats = [s.strip().lower() for s in args.only.split(",") if s.strip()]
        bad = [x for x in cats if x not in CATEGORIES]
        if bad:
            print(c(f"[!] 未知类型: {', '.join(bad)}；可用: {', '.join(CATEGORIES)}", 'red'))
            sys.exit(1)
        show_cats = set(cats)
    else:
        show_cats = set(DEFAULT_CATS)
        if args.all:
            show_cats.add('entry')    # --all 加进场
        if args.debug:
            show_cats.add('other')    # --debug 加未识别的 BaseDanmaku

    if sys.platform == 'win32':
        try:
            import colorama; colorama.just_fix_windows_console()
        except Exception:
            os.system("")   # 启用 Win10+ 终端的 ANSI

    platform = detect_platform(args.url)
    if not platform:
        print(c(f"[!] 无法识别平台(支持 bilibili/douyin/douyu/huya): {args.url}", 'red'))
        sys.exit(1)

    kwargs = {}
    cookie = args.cookie or auto_cookie(platform)
    if cookie and not os.path.exists(cookie):
        print(c(f"[!] cookie 文件不存在: {cookie}(将用游客身份)", 'yellow')); cookie = None
    if platform == "bilibili" and cookie:
        kwargs["bilibili_dm_cookie_path"] = cookie
    elif platform == "douyin" and cookie:
        kwargs["douyin_dm_cookies"] = cookie

    print(c(f"[*] 平台={platform} cookie={cookie or '游客'} 显示={','.join(sorted(show_cats))}", 'gray'))
    if args.dump_raw:
        print(c(f"[*] gift/member 原始消息将追加写入: {args.dump_raw}", 'gray'))

    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(listen(args.url, kwargs, show_cats, args.debug, args.dump_raw))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
