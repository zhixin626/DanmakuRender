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

# ════════════════════════════════════════════════════════════════════════════
#  B站 *_V2 消息：明文字段全部搬进了 data.pb（base64 包着的 protobuf）
#
#  decode_pb 做「无损解码」——把字节流按 protobuf 规则拆成 {字段路径: 值}。
#  protobuf 不携带字段名，只有编号，所以拿到的是 '1'/'2'/'22.2.2' 这样的路径；
#  「哪个编号是什么意思」只能对比多条真实消息反推，那部分放在下面的 guess_* 里。
# ════════════════════════════════════════════════════════════════════════════

def _read_varint(b, i):
    """protobuf 变长整数：每字节低 7 位是数据，最高位为 1 表示还有后续字节。"""
    val = shift = 0
    while True:
        x = b[i]; i += 1
        val |= (x & 0x7F) << shift
        if not x & 0x80:
            return val, i
        shift += 7


def decode_pb(pb_b64, max_depth=3):
    """base64 + protobuf 无损解码 → {字段路径: 值}，如 {'1': 388272783, '22.2.2': 'https://...'}。

    每个字段前有一个 key 字节：高位=字段编号，低 3 位=类型
      类型0 varint(整数)  类型2 长度分隔(字符串/嵌套)  类型1/5 定长(跳过)
    类型2 的内容能按 UTF-8 解成可打印文本就当字符串，否则视为嵌套结构递归展开
    （路径用点连接，如 22.2.2 = 字段22 里的字段2 里的字段2）。
    解不动就返回已解出的部分，不抛异常——协议变了也不会拖垮整条消息。
    """
    if not pb_b64:
        return {}
    try:
        data = base64.b64decode(pb_b64)
    except Exception:
        return {}

    def walk(b, path, depth, out):
        i = 0
        while i < len(b):
            try:
                key, i = _read_varint(b, i)
                field_no, wire_type = key >> 3, key & 7
                p = f'{path}{field_no}'
                if wire_type == 0:
                    out[p], i = _read_varint(b, i)
                elif wire_type == 2:
                    ln, i = _read_varint(b, i)
                    val = b[i:i + ln]; i += ln
                    try:
                        s = val.decode('utf-8')
                        if s and all(c == '\n' or c >= ' ' for c in s):
                            out[p] = s
                            continue
                    except UnicodeDecodeError:
                        pass
                    if depth < max_depth:
                        walk(val, p + '.', depth + 1, out)
                    else:
                        out[p] = val
                elif wire_type == 5:
                    i += 4
                elif wire_type == 1:
                    i += 8
                else:
                    break
            except (IndexError, ValueError):
                break
        return out

    return walk(data, '', 0, {})


def guess_entry_fields(f):
    """INTERACT_WORD_V2(进场) 的字段编号 → 含义。

    编号是拿多条真实消息对比反推的（uid/昵称/头像每条都不同，房间号每条都相同）：
        1        uid（与 22.1 一致）
        2        昵称（与 22.2.1 一致）
        6        房间号
        7        时间戳(秒)      8  时间戳(毫秒)
        22.2.2   头像 URL
        9.* / 22.3.*  粉丝牌（名称/等级/配色），暂不取
    """
    ts = f.get('7') or (f.get('8', 0) / 1000 if f.get('8') else None)
    return {
        'uid':       f.get('1') or f.get('22.1') or '',
        'uname':     f.get('2') or f.get('22.2.1') or '',
        'face_url':  f.get('22.2.2') or '',
        'timestamp': float(ts) if ts else None,
    }


def guess_gift_fields(f):
    """SEND_GIFT_V2(礼物) 的字段编号 → 含义。

    编号由真实样本对照确定（一次送 2 个小花花 vs 连击 2 次牛哇牛哇，两者行为不同）：
        1      uid              2      送礼人昵称        3      头像 URL
        10.1   礼物 ID          10.2   礼物名            10.3   本条数量
        10.5   单价(金瓜子)      10.7   本条总额 = 10.5×10.3
        10.8   货币类型(gold)   10.9   tid(去重用)       10.10  时间戳(秒)
        10.11  连击第几次        10.12  连击标识 combo_id
        10.14  连击累计额 = 10.7×10.11   10.18  动作词(投喂)
        10.29.1/10.33.2.1  主播名        10.29.2/10.33.1  主播 uid

    ⚠ 统计营收必须用 10.7（本条总额），不能用 10.14：连击的每一次都会单独推一条消息，
      按 10.14 累加会重复计算（连击两次 100 的礼物：10.14 是 100+200=300，实际只送了 200）。
      10.14 只适合用来显示「这串连击一共多少钱」。

    单位换算与旧 SEND_GIFT 一致：电池 = 金瓜子/100，人民币元 = 金瓜子/1000。
    """
    unit_coin  = f.get('10.5') or 0      # 单价（金瓜子）
    total_coin = f.get('10.7') or 0      # 本条总额（金瓜子）
    return {
        'uid':        f.get('1'),
        'uname':      f.get('2'),
        'face_url':   f.get('3'),
        'gift_id':    f.get('10.1'),
        'gift_name':  f.get('10.2'),
        'gift_count': f.get('10.3') or 0,
        'unit_coin':  unit_coin,
        'total_coin': total_coin,
        'battery':    unit_coin / 100,    # 单价 → 电池
        'price_cny':  total_coin / 1000,  # 本条总额 → 元
        'coin_type':  f.get('10.8'),
        'tid':        f.get('10.9'),
        'timestamp':  f.get('10.10'),
        'combo_num':  f.get('10.11'),
        'combo_id':   f.get('10.12'),
        'action':     f.get('10.18'),
    }


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

        for dm in dm_list:
            if dm.get('type') != 5:          # 非业务包(心跳回包等)直接跳过
                continue
            try:
                j = json.loads(dm.get('body'))
            except Exception:
                continue
            msg_type = j.get('cmd', '')
            payload = j.get('data', {}) or {}   # 这条消息的关键字段块(礼物/SC/大航海/进场都在 data 里)
            # 每个分支只做一件事：把原始 j 解析成 DMR 的弹幕类型；理解不了的统一塞 BaseDanmaku.raw
            try:
                if msg_type == 'DANMU_MSG':                    # 普通弹幕
                    info = j['info']
                    meta = info[0]
                    content = text = info[1]
                    emoticon = None
                    if meta[12] == 1:                           # 整条都是 B 站 emoticon
                        emo = meta[13]
                        name = info[1]
                        display = name if name.startswith('[') and name.endswith(']') else f'[{name}]'
                        content = text = display
                        emoticon = {
                            'emoticon_unique': emo.get('emoticon_unique'),
                            'name': name,
                            'url': emo.get('url'),
                            'width': emo.get('width'),
                            'height': emo.get('height'),
                            'is_dynamic': emo.get('is_dynamic', 0),
                        }
                    msgs.append(UserDanmaku(
                        dtype='danmaku', uname=info[2][1], uid=info[2][0],
                        content=content, text=text, color=f"{meta[3]:06x}",
                        timestamp=meta[4] / 1000, emoticon=emoticon, raw=j))

                elif msg_type == 'SUPER_CHAT_MESSAGE':         # 醒目留言 SC
                    base = payload.get('uinfo', {}).get('base', {})
                    text = payload.get('message', '')
                    price_cny = payload.get('price', 0)           # 真实元价格
                    msgs.append(SuperChatDanmaku(
                        uname=base.get('name', ''), uid=payload.get('uid', ''),
                        content=text, text=text,
                        price=price_cny * 10, price_unit='电池', price_cny=price_cny,
                        sc_duration=max(30, min(180, payload.get('time', 60))),
                        background_color=payload.get('background_color', 'FFF5ED'),
                        background_bottom_color=payload.get('background_bottom_color', 'B2602A'),
                        name_color='666666', content_color='FFFFFF',
                        face_url=base.get('face', ''),
                        timestamp=payload.get('ts') or datetime.now().timestamp(), raw=j))

                elif msg_type == 'USER_TOAST_MSG':             # 大航海(舰长/提督/总督)：开通/续费/大航海盲盒爆出
                    # 用 USER_TOAST_MSG 取代 GUARD_BUY：它对「新开通」和「续费/盲盒爆出」都会发，
                    # 而 GUARD_BUY 只在新开通时发——所以只认这条既不漏(续费/盲盒)也不重(新开通别再走 GUARD_BUY)。
                    uname       = payload.get('username', '未知用户')
                    member_name = payload.get('role_name', '舰长')     # 舰长/提督/总督
                    num         = payload.get('num', 1)
                    unit        = payload.get('unit', '月')            # '月'，或盲盒/续费的 '*8天'
                    price       = (payload.get('price') or 0) / 1000   # 金瓜子 → 元（198000→198, 52800→52.8）
                    action      = '续费' if payload.get('op_type') == 2 else '开通'   # op_type: 1=开通, 2=续费
                    text = f"{action}{member_name}({price:g}元)×{num}{unit}"
                    msgs.append(MemberDanmaku(
                        uname=uname, uid=payload.get('uid', ''), content=text, text=text,
                        member_name=member_name, member_price=price, member_count=num,
                        member_unit=unit, price_cny=price,
                        timestamp=payload.get('start_time') or datetime.now().timestamp(), raw=j))

                elif msg_type == 'SEND_GIFT':                  # 礼物
                    uname = payload.get('sender_uinfo', {}).get('base', {}).get('name') or payload.get('uname', '未知用户')
                    gift_name = payload.get('giftName', '未知礼物')
                    num = payload.get('num', 0)
                    battery = payload.get('price', 0) / 100       # 金瓜子单价 → 电池(1电池=100金瓜子)
                    total_coin = payload.get('total_coin') or (payload.get('price', 0) * num) or 0
                    text = f"送出{gift_name}({battery:g}电池)x{num}"
                    msgs.append(GiftDanmaku(
                        uname=uname, uid=payload.get('sender_uinfo', {}).get('uid') or payload.get('uid', ''),
                        content=text, text=text, gift_name=gift_name, gift_count=num,
                        gift_price=battery, price_unit='电池', price_cny=total_coin / 1000,
                        color='d9a6c4',
                        timestamp=payload.get('timestamp') or payload.get('ts') or datetime.now().timestamp(), raw=j))

                elif msg_type == 'SEND_GIFT_V2':               # 礼物新格式(数据在 data.pb 的 protobuf 里)
                    # 与上面的 SEND_GIFT 分支并存：B站在灰度推送，部分房间已切 V2，新旧格式会共存一段时间。
                    g = guess_gift_fields(decode_pb(payload.get('pb')))
                    gift_name = g['gift_name'] or '未知礼物'
                    num = g['gift_count']
                    text = f"送出{gift_name}({g['battery']:g}电池)x{num}"
                    msgs.append(GiftDanmaku(
                        uname=g['uname'] or '未知用户', uid=g['uid'] or '',
                        content=text, text=text,
                        gift_name=gift_name, gift_count=num,
                        gift_price=g['battery'], price_unit='电池',
                        price_cny=g['price_cny'],   # 用本条总额算，不用连击累计额(会重复计数)
                        color='d9a6c4', face_url=g['face_url'],
                        timestamp=g['timestamp'] or datetime.now().timestamp(), raw=j))

                elif msg_type == 'INTERACT_WORD_V2':           # 进场(正文在 data.pb 的 protobuf 里)
                    e = guess_entry_fields(decode_pb(payload.get('pb')))
                    msgs.append(EntryDanmaku(
                        uname=e['uname'] or '未知用户', uid=e['uid'],
                        face_url=e['face_url'] or None,
                        timestamp=e['timestamp'] or datetime.now().timestamp(), raw=j))

                else:                                          # 理解不了的：原样塞进 BaseDanmaku.raw
                    # dtype 用 msg_type 小写、content 放 data，--all 下可直接看结构(大航海toast/盲盒/rank/notice…)
                    msgs.append(BaseDanmaku(
                        dtype=(msg_type or 'other').lower(), raw=j,
                        timestamp=datetime.now().timestamp(), content=payload or j))

            except Exception as e:
                logger.debug(f"错误信息:{e}")

        return msgs
