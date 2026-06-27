from datetime import datetime
import json, re, select, random, traceback
import asyncio, aiohttp, zlib, brotli
from struct import pack, unpack
from DMR.utils import random_user_agent, BaseDanmaku, UserDanmaku, SuperChatDanmaku, GiftDanmaku, MemberDanmaku, EntryDanmaku
from DMR.LiveAPI.bilivideo_utils import encode_wbi, getWbiKeys
from .DMAPI import DMAPI
from DMR.utils.bark_notifier  import bark_notify
import base64

import logging
logger = logging.getLogger(__name__)

def parse_enter_msg(j):
    pb_data = j.get("data", {}).get("pb")
    if not pb_data:
        return "", ""

    try:
        # 1. Base64 解码 (Base64 Decoding)
        decoded = base64.b64decode(pb_data)

        # 2. 提取所有潜在的文本块 (Extract potential text chunks)
        # 范围包括：ASCII 可见字符、B站常用的控制字符、多字节 UTF-8 字符
        chunks = re.findall(rb'[\x20-\x7e\x80-\xff]{2,}', decoded)

        readable_list = []
        for c in chunks:
            try:
                # 使用 ignore 模式防止个别字节导致整体解码失败
                s = c.decode('utf-8', errors='ignore').strip()
                # 清洗掉两端的引号、空格以及常见的 Protobuf 干扰符
                s = s.strip('"\\' + "\x00\x01\x02\x12\x0c")
                if len(s) > 1:
                    readable_list.append(s)
            except Exception:
                continue

        face_url = "未知头像"
        uname = "未知用户"

        # 3. 定位逻辑 (Positioning Logic)
        for i, s in enumerate(readable_list):
            # 只要包含域名且包含 http 协议即可判定为头像 URL
            if "hdslb.com" in s and "http" in s:
                # 如果 URL 前面有残留字符，截取真正的 URL 开始位置
                start_idx = s.find("http")
                face_url = s[start_idx:]

                # 关键：名字通常就在头像 URL 所在块的前面 1 或 2 个位置
                if i > 0:
                    # 优先取上一个，如果上一个看起来像 UID (纯数字)，再往前看一个
                    candidate = readable_list[i-1]
                    if candidate.isdigit() and i > 1:
                        uname = readable_list[i-2]
                    else:
                        uname = candidate
                break

        # 4. 保底逻辑 (Fallback)
        # 如果还是“未知用户”，取列表中第一个长得像名字的非 URL 字符串
        if uname == "未知用户" and readable_list:
            for s in readable_list:
                if "http" not in s and not s.isdigit() and len(s) > 1:
                    uname = s
                    break

        return uname, face_url

    except Exception as e:
        return f"解析失败: {str(e)}", ""

class Bilibili(DMAPI):
    heartbeat = b"\x00\x00\x00\x1f\x00\x10\x00\x01\x00\x00\x00\x02\x00\x00\x00\x01\x5b\x6f\x62\x6a\x65\x63\x74\x20\x4f\x62\x6a\x65\x63\x74\x5d"
    headers = {
        'accept': '*/*',
        'accept-encoding': 'gzip, deflate',
        'accept-language': 'zh-CN,zh;q=0.8,en-US;q=0.5,en;q=0.3',
        'user-agent': random_user_agent(),
        'origin': 'https://live.bilibili.com',
        'referer': 'https://live.bilibili.com',
    }
    interval = 30



    async def get_ws_info(url, **kwargs):
        url = "https://api.live.bilibili.com/room/v1/Room/room_init?id=" + url.split("/")[-1]
        reg_datas = []
        async with aiohttp.ClientSession(headers=Bilibili.headers) as session:
            async with session.get(url) as resp:
                room_json = await resp.json()
                room_id = room_json["data"]["room_id"]

        encoded_parms = encode_wbi(
            params = {
                "id": room_id,
                'type': 0,
                'web_location': 444.8,
            },
            wbi_img=getWbiKeys(),
        )
        async with aiohttp.ClientSession(headers=Bilibili.headers) as session:
            uid=0 # 游客身份
            cookie_path = kwargs.get('bilibili_dm_cookie_path')
            if cookie_path:
                try:
                    with open(cookie_path, 'r') as f:
                        cookies_list = json.load(f).get('cookie_info', {}).get('cookies', [])
                    target_keys = {'SESSDATA', 'bili_jct', 'DedeUserID'}
                    cookie_dict = {c['name']: c['value'] for c in cookies_list if c.get('name') in target_keys}
                    Bilibili.headers['cookie'] = ";".join([f"{k}={v}" for k, v in cookie_dict.items()]) + ";"
                    uid = int(cookie_dict['DedeUserID'])  # 登录身份
                    logger.info(f"[*] 正在使用 {cookie_path} 的cookies获取b站弹幕")
                except FileNotFoundError:
                    logger.info(f"[*] 警告：找不到 Cookie 文件 {cookie_path}，将使用游客身份获取弹幕")
                except Exception as e:
                    logger.info(f"[*] 解析 Cookie 出错: {e}，将使用游客身份")
            else:
                # 如果没传参数，直接打印
                logger.info("[*] 未配置 bilibili_dm_cookie_path，将使用游客身份获取弹幕")

            current_cookie = Bilibili.headers.get('cookie', '')
            # 2025-06-28 B站新风控需要cookies中存在buvid3
            if 'buvid3' not in current_cookie or 'buvid4' not in current_cookie:
                async with session.get("https://api.bilibili.com/x/frontend/finger/spi",timeout=5) as resp:
                    buvid_json = await resp.json()
                    current_cookie += f"buvid3={buvid_json['data']['b_3']};buvid4={buvid_json['data']['b_4']};"
                    Bilibili.headers['cookie'] = current_cookie
            async with session.get('https://api.live.bilibili.com/xlive/web-room/v1/index/getDanmuInfo',headers=Bilibili.headers, params=encoded_parms) as resp:
                room_json = await resp.json()
                token = room_json['data']['token']


        # 从 cookie 中提取出 buvid3 的值
        buvid_val = current_cookie.split('buvid3=')[1].split(';')[0]

        data = json.dumps({
            "uid": uid,
            "roomid": room_id,
            "protover": 3,
            "buvid": buvid_val,
            "platform": "web",
            "type": 2,
            "key": token,
        },separators=(",", ":"),).encode("ascii")

        data = (
            pack(">i", len(data) + 16)
            + pack(">h", 16)
            + pack(">h", 1)
            + pack(">i", 7)
            + pack(">i", 1)
            + data
        )
        reg_datas.append(data)

        return "wss://broadcastlv.chat.bilibili.com/sub", reg_datas

    def decode_msg(data):
        dm_list = []
        msgs = []

        def decode_packet(packet_data):
            dm_list = []
            while True:
                try:
                    packet_len, header_len, ver, op, seq = unpack('!IHHII', packet_data[0:16])
                except Exception:
                    break
                if len(packet_data) < packet_len:
                    break

                if ver == 2:
                    dm_list.extend(decode_packet(zlib.decompress(packet_data[16:packet_len])))\
                # version3: 参考https://github.com/biliup/biliup/blob/master/biliup/plugins/Danmaku/bilibili.py
                elif ver == 3:
                    dm_list.extend(decode_packet(brotli.decompress(packet_data[16:packet_len])))
                elif ver == 0 or ver == 1:
                    dm_list.append({
                        'type': op,
                        'body': packet_data[16:packet_len]
                    })
                else:
                    break

                if len(packet_data) == packet_len:
                    break
                else:
                    packet_data = packet_data[packet_len:]
            return dm_list

        dm_list = decode_packet(data)

        now = datetime.now().timestamp()
        for dm in dm_list:
            if dm.get('type') != 5:          # 非业务包(心跳回包等)直接跳过
                continue
            try:
                j = json.loads(dm.get('body'))
            except Exception:
                continue
            cmd = j.get('cmd', '')
            data = j.get('data', {}) or {}
            # 每个分支只做一件事：把原始 j 解析成 DMR 的弹幕类型；理解不了的统一塞 BaseDanmaku.raw
            try:
                if cmd == 'DANMU_MSG':                         # 普通弹幕
                    info = j['info']
                    color = f"{info[0][3]:06x}"
                    dtype, content, text = 'danmaku', info[1], info[1]
                    emo = info[0][13] if len(info[0]) > 13 else None   # 表情弹幕的表情 dict
                    if isinstance(emo, dict) and emo.get('url'):
                        content = json.dumps({'url': emo['url'], 'desc': info[1]}, ensure_ascii=False)
                        text, dtype = f'[{info[1]}]', 'emoticon'
                    msgs.append(UserDanmaku(
                        dtype=dtype, uname=info[2][1], uid=info[2][0],
                        content=content, text=text, color=color,
                        timestamp=info[0][4] / 1000, raw=j))

                elif cmd == 'SUPER_CHAT_MESSAGE':              # 醒目留言 SC
                    base = data.get('uinfo', {}).get('base', {})
                    text = data.get('message', '')
                    price_cny = data.get('price', 0)           # 真实元价格
                    msgs.append(SuperChatDanmaku(
                        uname=base.get('name', ''), uid=data.get('uid', ''),
                        content=text, text=text,
                        price=price_cny * 10, price_unit='电池', price_cny=price_cny,
                        sc_duration=max(30, min(180, data.get('time', 60))),
                        background_color=data.get('background_color', 'FFF5ED'),
                        background_bottom_color=data.get('background_bottom_color', 'B2602A'),
                        name_color='666666', content_color='FFFFFF',
                        face_url=base.get('face', ''),
                        timestamp=data.get('ts') or now, raw=j))

                elif cmd == 'GUARD_BUY':                       # 开通大航海(舰长/提督/总督)
                    uname = data.get('username', '未知用户')
                    price = data.get('price', 0) / 1000        # 金瓜子 → 元
                    member_name = data.get('gift_name', '舰长')
                    num = data.get('num', 1)
                    text = f"{uname} 开通了{num}个月的{member_name}价值{price}元"
                    msgs.append(MemberDanmaku(
                        uname=uname, uid=data.get('uid', ''), content=text, text=text,
                        member_name=member_name, member_price=price, member_count=num,
                        member_unit='月', price_cny=price,
                        timestamp=data.get('start_time') or now, raw=j))

                elif cmd == 'SEND_GIFT':                       # 礼物
                    uname = data.get('sender_uinfo', {}).get('base', {}).get('name') or data.get('uname', '未知用户')
                    gift_name = data.get('giftName', '未知礼物')
                    num = data.get('num', 0)
                    battery = data.get('price', 0) / 100       # 金瓜子单价 → 电池(1电池=100金瓜子)
                    total_coin = data.get('total_coin') or (data.get('price', 0) * num) or 0
                    text = f"<{uname}>送给主播价值{battery:.0f}电池的{gift_name}x{num}"
                    msgs.append(GiftDanmaku(
                        uname=uname, uid=data.get('sender_uinfo', {}).get('uid') or data.get('uid', ''),
                        content=text, text=text, gift_name=gift_name, gift_count=num,
                        gift_price=f"{battery:.0f}", price_unit='电池', price_cny=total_coin / 1000,
                        color='d9a6c4',
                        timestamp=data.get('timestamp') or data.get('ts') or now, raw=j))

                elif cmd == 'INTERACT_WORD_V2':                # 进场(正文藏在 data.pb base64 里)
                    name, face = parse_enter_msg(j)
                    ts = data.get('timestamp') or data.get('trigger_time')
                    if ts:
                        ts = float(ts)
                        ts = ts / 1e9 if ts > 1e15 else (ts / 1e3 if ts > 1e12 else ts)
                    msgs.append(EntryDanmaku(
                        uname=name, uid=data.get('uid', '') or '',
                        face_url=face, timestamp=ts or now, raw=j))

                else:                                          # 理解不了的：原样塞进 BaseDanmaku.raw
                    # dtype 用 cmd 小写、content 放 data，--all 下可直接看结构(大航海toast/盲盒/rank/notice…)
                    msgs.append(BaseDanmaku(
                        dtype=(cmd or 'other').lower(), raw=j,
                        timestamp=now, content=data or j))

            except Exception:
                continue

        return msgs
