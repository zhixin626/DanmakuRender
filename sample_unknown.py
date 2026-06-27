# -*- coding: utf-8 -*-
"""多直播间「未识别弹幕」采样器（用于反推协议，比如斗鱼钻石粉丝 dfrbc）。

同时监控多个直播间，只关注 decoder 解析不出的 BaseDanmaku，按「平台+类型」各存最多 N 条
到一个文件夹。程序重启会先读已存的、统计每类已存几条，接着存——不重复、不超额。
断线会自动重连。

用法:
    python sample_unknown.py <URL1> <URL2> ...
    python sample_unknown.py rooms.txt          # 一行一个 URL 的文本(# 开头为注释)

选项(环境变量):
    SAMPLE_DIR   输出文件夹(默认 unknown_danmaku_samples)
    SAMPLE_LIMIT 每类最多存几条(默认 10)
"""
import asyncio
import glob
import json
import os
import re
import sys

os.environ['http_proxy'] = ''
os.environ['https_proxy'] = ''
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from DMR.LiveAPI.danmaku import DanmakuClient
from DMR.utils import BaseDanmaku
from run_danmu import to_danmaku, detect_platform, auto_cookie

SAMPLE_DIR = os.environ.get("SAMPLE_DIR", "unknown_danmaku_samples")
LIMIT = int(os.environ.get("SAMPLE_LIMIT", "10"))
RECONNECT_DELAY = 5


def _safe(name):
    return re.sub(r'[^\w.\-]', '_', str(name))[:80] or 'unknown'


def _jsonable(o):
    """json.dumps 的兜底：抖音未识别消息的 raw 是 protobuf 对象，按通用 wire format 解成可读 dict。"""
    payload = getattr(o, 'payload', None)
    if isinstance(payload, (bytes, bytearray)):
        try:
            from DMR.LiveAPI.danmaku.douyin import _decode_protobuf_raw
            return {"method": getattr(o, 'method', ''), "decoded": _decode_protobuf_raw(payload)}
        except Exception:
            pass
    return str(o)


class Sampler:
    """按 (平台, 类型) 各存最多 limit 条；重启时从已有文件恢复计数后接着存。"""
    def __init__(self, folder=SAMPLE_DIR, limit=LIMIT):
        self.folder = folder
        self.limit = limit
        os.makedirs(folder, exist_ok=True)
        self.counts = {}          # (platform, dtype) -> 已存条数
        self._load_existing()

    def _path(self, platform, dtype):
        return os.path.join(self.folder, f"{_safe(platform)}__{_safe(dtype)}.jsonl")

    def _load_existing(self):
        for path in glob.glob(os.path.join(self.folder, "*.jsonl")):
            base = os.path.basename(path)[:-6]      # 去掉 .jsonl
            if "__" not in base:
                continue
            platform, dtype = base.split("__", 1)
            try:
                with open(path, encoding="utf-8") as f:
                    n = sum(1 for line in f if line.strip())
            except Exception:
                n = 0
            self.counts[(platform, dtype)] = n
        if self.counts:
            print(f"[*] 已有 {len(self.counts)} 种未识别类型、共 {sum(self.counts.values())} 条样本，接着存。")

    def save(self, platform, dm):
        dtype = getattr(dm, 'dtype', 'unknown')
        key = (platform, dtype)
        n = self.counts.get(key, 0)
        if n >= self.limit:
            return
        rec = {
            "platform": platform,
            "dtype": dtype,
            "time": getattr(dm, 'timestamp', None),
            "content": getattr(dm, 'content', None),
            "raw": getattr(dm, 'raw', None),
        }
        try:
            line = json.dumps(rec, ensure_ascii=False, default=_jsonable)
        except Exception as e:
            print(f"[!] 序列化失败 {platform}/{dtype}: {e}")
            return
        with open(self._path(platform, dtype), "a", encoding="utf-8") as f:
            f.write(line + "\n")
        self.counts[key] = n + 1
        tag = "已存满" if self.counts[key] >= self.limit else ""
        print(f"[+] {platform}/{dtype} ({self.counts[key]}/{self.limit}) {tag}")


def _cookie_kwargs(platform):
    kwargs = {}
    cookie = auto_cookie(platform)
    if cookie and os.path.exists(cookie):
        if platform == "bilibili":
            kwargs["bilibili_dm_cookie_path"] = cookie
        elif platform == "douyin":
            kwargs["douyin_dm_cookies"] = cookie
    return kwargs


async def _consume(q, platform, sampler):
    while True:
        dm = to_danmaku(await q.get())
        if dm is not None and type(dm) is BaseDanmaku:   # 只采"解析不出"的(子类都是已识别类型)
            sampler.save(platform, dm)


async def monitor(url, sampler):
    platform = detect_platform(url)
    if not platform:
        print(f"[!] 跳过无法识别平台的 URL: {url}")
        return
    kwargs = _cookie_kwargs(platform)
    while True:    # 断线自动重连
        q = asyncio.Queue()
        client = None
        consumer = None
        try:
            client = DanmakuClient(url, q, **kwargs)
            consumer = asyncio.create_task(_consume(q, platform, sampler))
            print(f"[*] 连接 {platform}: {url}")
            await client.start()      # 阻塞到连接断开/出错
        except Exception as e:
            print(f"[!] {url} 断开/出错: {e}")
        finally:
            if consumer:
                consumer.cancel()
            if client:
                try:
                    await client.stop()
                except Exception:
                    pass
        await asyncio.sleep(RECONNECT_DELAY)


def _load_urls(args):
    if len(args) == 1 and args[0].lower().endswith('.txt') and os.path.exists(args[0]):
        with open(args[0], encoding="utf-8") as f:
            return [ln.strip() for ln in f if ln.strip() and not ln.strip().startswith('#')]
    return list(args)


async def amain(urls):
    sampler = Sampler()
    await asyncio.gather(*(monitor(u, sampler) for u in urls))


if __name__ == "__main__":
    urls = _load_urls(sys.argv[1:])
    if not urls:
        print(__doc__)
        sys.exit(0)
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    print(f"[*] 监控 {len(urls)} 个直播间，未识别弹幕每类最多存 {LIMIT} 条 -> {SAMPLE_DIR}/")
    try:
        asyncio.run(amain(urls))
    except KeyboardInterrupt:
        print("\n[*] 已停止。")
