# -*- coding: utf-8 -*-
"""连 B 站前 N 个热门直播间，专抓「心动盲盒」相关弹幕。

盲盒在 B 站一般是 SEND_GIFT 里带 blind_gift 字段（盲盒名 + 开出的礼物）；开出舰长可能走
GUARD_BUY 或 USER_TOAST_MSG。本脚本把带 blind/盲盒 的消息按 cmd 各存若干条，
另存 GUARD_BUY / USER_TOAST_MSG 供对照，落到 bili_blindbox_samples/。

用法:
    python bili_catch_blindbox.py
    CATCH_MINUTES=45 python bili_catch_blindbox.py
"""
import asyncio
import json
import os
import sys
import time

os.environ['http_proxy'] = ''
os.environ['https_proxy'] = ''
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import requests
from DMR.LiveAPI.danmaku import DanmakuClient

COOKIE = '.login_info/b站_不可一世冷狗.json'
SAMPLE_DIR = 'bili_blindbox_samples'
LIMIT = 15
CATCH_MINUTES = int(os.environ.get('CATCH_MINUTES', '30'))
KEY_BLIND = ('blind_gift', '盲盒', 'BLIND')        # 盲盒标志
EXTRA_CMDS = ('GUARD_BUY', 'USER_TOAST_MSG')      # 盲盒开舰长可能走这俩，存来对照

counts = {}


def get_hot_rooms(n=10):
    h = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://live.bilibili.com/'}
    r = requests.get('https://api.live.bilibili.com/room/v1/area/getListByAreaID',
                     params={'areaId': 0, 'sort': 'online', 'pageSize': n, 'page': 1},
                     headers=h, timeout=8)
    return [str(it['roomid']) for it in (r.json().get('data') or [])][:n]


def save(tag, j):
    n = counts.get(tag, 0)
    if n >= LIMIT:
        return
    os.makedirs(SAMPLE_DIR, exist_ok=True)
    with open(os.path.join(SAMPLE_DIR, f'{tag}.jsonl'), 'a', encoding='utf-8') as f:
        f.write(json.dumps(j, ensure_ascii=False) + '\n')
    counts[tag] = n + 1
    print(f'[+] {tag} ({counts[tag]}/{LIMIT})')
    if '盲盒' in json.dumps(j, ensure_ascii=False) or 'blind_gift' in json.dumps(j, ensure_ascii=False):
        gn = (j.get('data') or {}).get('giftName') or ''
        print(f'    ★ 盲盒相关: cmd={j.get("cmd")} giftName={gn}')


async def consume(q):
    while True:
        dm = await q.get()
        raw = getattr(dm, 'raw', None)
        if not isinstance(raw, dict):
            continue
        cmd = str(raw.get('cmd') or raw.get('msg_type') or 'unknown').split(':')[0]
        data = raw.get('data') or {}
        bg = data.get('blind_gift')      # 注意：普通 SEND_GIFT 也有这个键但为 null，非空才是真盲盒
        s = json.dumps(raw, ensure_ascii=False)
        if bg:                            # 真盲盒（开出礼物）——按盒名分桶，避免某种盒子占满
            box = (bg.get('original_gift_name') or 'box').replace('/', '_')
            save('BLIND_' + box, raw)
        elif '盲盒' in s:                  # 名字里直接带「盲盒」的其它消息
            save('NAME_' + cmd, raw)
        elif cmd in EXTRA_CMDS:           # 大航海/上舰 toast，盲盒开舰长可能走这里
            save(cmd, raw)


async def monitor(rid):
    url = f'https://live.bilibili.com/{rid}'
    kwargs = {}
    if os.path.exists(COOKIE):
        kwargs['bilibili_dm_cookie_path'] = COOKIE
    while True:
        q = asyncio.Queue()
        client = None
        consumer = None
        try:
            client = DanmakuClient(url, q, **kwargs)
            consumer = asyncio.create_task(consume(q))
            print(f'[*] 连接 {url}')
            await client.start()
        except Exception as e:
            print(f'[!] {url} 断开: {e}')
        finally:
            if consumer:
                consumer.cancel()
            if client:
                try:
                    await client.stop()
                except Exception:
                    pass
        await asyncio.sleep(5)


async def reporter(deadline):
    while time.time() < deadline:
        await asyncio.sleep(60)
        left = int((deadline - time.time()) / 60)
        print(f'[i] 已采: {dict(counts)}  剩 ~{left} 分钟')


async def amain():
    # 命令行给了房间号/URL 就抓指定的，否则抓热门前 10
    args = [a.rstrip('/').split('/')[-1] for a in sys.argv[1:] if a.strip()]
    if args:
        rids = args
        print(f'[*] 指定房间: {rids}')
    else:
        try:
            rids = get_hot_rooms(10)
        except Exception as e:
            print(f'[!] 热门接口失败: {e}')
            return
    print(f'[*] 监控 B 站 {len(rids)} 个房 {CATCH_MINUTES} 分钟: {rids}')
    deadline = time.time() + CATCH_MINUTES * 60
    tasks = [asyncio.create_task(monitor(r)) for r in rids]
    tasks.append(asyncio.create_task(reporter(deadline)))
    try:
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=CATCH_MINUTES * 60 + 10)
    except asyncio.TimeoutError:
        pass
    print('[*] 结束。最终样本:', dict(counts))


if __name__ == '__main__':
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        print('\n[*] 手动停止。')
