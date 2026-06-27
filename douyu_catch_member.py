# -*- coding: utf-8 -*-
"""连一批斗鱼热门直播间，挂着等「开通/续费钻石粉丝」这类未识别弹幕出现。

斗鱼 decoder(douyu.py)对识别不了的消息类型会自动采样到 douyu_unknown_samples/<type>.jsonl
(每类最多 10 条)。本脚本只负责把尽量多的热门房连上、把队列抽空，让采样器去收。
跑一阵后去 douyu_unknown_samples/ 找带「价格/月数/等级名」的那条，就是钻石粉丝。

用法:
    python douyu_catch_member.py            # 默认抓热门 + 备用房，跑 30 分钟
    CATCH_MINUTES=60 python douyu_catch_member.py
"""
import asyncio
import os
import sys
import time

os.environ['http_proxy'] = ''
os.environ['https_proxy'] = ''
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import requests
from DMR.LiveAPI.danmaku import DanmakuClient

BACKUP_ROOMS = ['36252', '793400']        # 用户提供的备用热门房
CATCH_MINUTES = int(os.environ.get('CATCH_MINUTES', '30'))
SAMPLE_DIR = 'douyu_unknown_samples'
# 钻石粉丝/会员相关关键词，命中就高亮提示
HINT_WORDS = ['钻石', '粉丝', '开通', '续费', '贵宾', '超级', '会员', '个月', '荣誉']


def get_hot_rooms(n=18):
    """斗鱼热门房 room_id 列表，失败则只用备用房。"""
    rids = []
    try:
        r = requests.get('https://capi.douyucdn.cn/api/v1/getbigDataRoom',
                         headers={'User-Agent': 'Mozilla/5.0'}, timeout=8)
        for it in (r.json().get('data') or []):
            rid = str(it.get('room_id') or '').strip()
            if rid:
                rids.append(rid)
    except Exception as e:
        print(f'[!] 热门接口失败({e})，只用备用房')
    for b in BACKUP_ROOMS:
        if b not in rids:
            rids.append(b)
    return rids[:n]


def list_samples():
    """当前已采样的类型 -> 条数。"""
    out = {}
    if not os.path.isdir(SAMPLE_DIR):
        return out
    for fn in os.listdir(SAMPLE_DIR):
        if fn.endswith('.jsonl'):
            p = os.path.join(SAMPLE_DIR, fn)
            try:
                with open(p, encoding='utf-8') as f:
                    out[fn[:-6]] = sum(1 for ln in f if ln.strip())
            except Exception:
                out[fn[:-6]] = -1
    return out


def scan_membership_hits():
    """扫已采样本，找含会员关键词的类型(很可能就是钻石粉丝)。"""
    hits = []
    if not os.path.isdir(SAMPLE_DIR):
        return hits
    for fn in os.listdir(SAMPLE_DIR):
        if not fn.endswith('.jsonl'):
            continue
        try:
            txt = open(os.path.join(SAMPLE_DIR, fn), encoding='utf-8').read()
        except Exception:
            continue
        if any(w in txt for w in HINT_WORDS):
            hits.append(fn[:-6])
    return hits


async def monitor(rid):
    url = f'https://www.douyu.com/{rid}'
    while True:
        q = asyncio.Queue()
        client = None
        try:
            client = DanmakuClient(url, q)
            drain = asyncio.create_task(_drain(q))
            print(f'[*] 连接 {url}')
            await client.start()
        except Exception as e:
            print(f'[!] {url} 断开: {e}')
        finally:
            try:
                drain.cancel()
            except Exception:
                pass
            if client:
                try:
                    await client.stop()
                except Exception:
                    pass
        await asyncio.sleep(5)


async def _drain(q):
    while True:
        await q.get()      # 丢弃；采样在 decoder 内部已完成


async def reporter(deadline):
    before = set(list_samples())
    while time.time() < deadline:
        await asyncio.sleep(60)
        cur = list_samples()
        new = sorted(set(cur) - before)
        if new:
            print(f'[+] 新增未识别类型: {new}')
            before |= set(new)
        hits = scan_membership_hits()
        if hits:
            print(f'[★] 疑似会员/钻石粉丝类型(含关键词): {hits}  -> 去 {SAMPLE_DIR}/ 查看')
        left = int((deadline - time.time()) / 60)
        print(f'[i] 已采 {len(cur)} 类，剩 ~{left} 分钟')


async def amain():
    rids = get_hot_rooms()
    print(f'[*] 监控 {len(rids)} 个斗鱼房 {CATCH_MINUTES} 分钟: {rids}')
    deadline = time.time() + CATCH_MINUTES * 60
    tasks = [asyncio.create_task(monitor(r)) for r in rids]
    tasks.append(asyncio.create_task(reporter(deadline)))
    try:
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=CATCH_MINUTES * 60 + 10)
    except asyncio.TimeoutError:
        pass
    print('[*] 时间到，结束。最终样本:')
    for t, n in sorted(list_samples().items()):
        print(f'    {t}: {n}')
    hits = scan_membership_hits()
    print(f'[★] 含会员关键词的类型: {hits or "(未捕获，可加时间重跑)"}')


if __name__ == '__main__':
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        print('\n[*] 手动停止。')
