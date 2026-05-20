from datetime import datetime
import json, re, select, random, traceback
import asyncio, aiohttp, zlib, brotli
from struct import pack, unpack
from DMR.utils import random_user_agent, SuperChatDanmaku, SimpleDanmaku,GiftDanmaku
from DMR.LiveAPI.bilivideo_utils import encode_wbi, getWbiKeys
from .DMAPI import DMAPI
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
            except:
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

        for i, dm in enumerate(dm_list):
            try:
                msg = {}
                if dm.get('type') == 5:
                    j = json.loads(dm.get('body'))

                    # print(j.get('cmd')) #debug

                    msg['msg_type'] = {
                        'SEND_GIFT': 'gift',
                        'DANMU_MSG': 'danmaku',
                        'INTERACT_WORD_V2': 'enter',
                        'NOTICE_MSG': 'broadcast',
                        'SUPER_CHAT_MESSAGE': 'super_chat',  # 新增此行
                    }.get(j.get('cmd'), 'other')  # 类型判断

                    if 'DANMU_MSG' in j.get('cmd'): # 类型判断的兜底
                        msg["msg_type"] = "danmaku"

                    if msg["msg_type"] == "danmaku": # 普通弹幕类型
                        # print(j)
                        msg["name"] = j.get("info", ["", "", ["", ""]])[2][1] or j.get(
                            "data", {}
                        ).get("uname", "")
                        msg["color"] = f"{j.get('info', [[0, 0, 0, 16777215]])[0][3]:06x}"

                        if msg.get("color") == "e33fff": # 把紫色改浅一点
                            msg["color"] = "e866ff"

                        msg["content"] = j.get("info")[1]
                        try:
                            msg['timestamp'] = j.get('info')[0][4]/1000
                            if j.get('info')[13] != r'{}':
                                emoticon_info = j.get('info')[0][13]
                                emoticon_url = emoticon_info['url']
                                emoticon_desc = j.get('info')[1]
                                msg["content"] = json.dumps({'url':emoticon_url,'desc':emoticon_desc},ensure_ascii=False)
                                msg['text'] = f'[{emoticon_desc}]'
                                msg['msg_type'] = 'emoticon'
                        except Exception as e:
                            pass

                        dm = SimpleDanmaku(
                            dtype=msg.get('msg_type', 'other'),
                            uname=msg.get('name', ''),
                            content=msg.get('content', ''),
                            timestamp=msg.get('timestamp', datetime.now().timestamp()),
                            color=msg.get('color', 'ffffff'),
                            uid=j.get("info")[2][0],
                        )
                        msgs.append(dm)
                        continue

                    elif msg['msg_type'] == 'enter':
                        # pass
                        # print(msg)
                        name, face = parse_enter_msg(j)
                        if "巧丽哇" in name:
                            logger.info(f"👋 {name} 进入直播间！")
                            logger.info(f"🖼️ 头像: {face}")
                            logger.info(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

                    elif msg['msg_type'] == 'interactive_danmaku': # 这个分支没用！
                        msg["msg_type"] = "danmaku"
                        msg['name'] = j.get('data', {}).get('uname', '')
                        msg['content'] = j.get('data', {}).get('msg', '')
                        msg["color"] = 'ffffff'

                    elif msg["msg_type"] == "broadcast":
                        msg["type"] = j.get("msg_type", 0)
                        msg["roomid"] = j.get("real_roomid", 0)
                        msg["content"] = j.get("msg_common", "none")
                        msg["raw"] = j

                    elif msg["msg_type"] == "super_chat":  # 新增此部分
                        msg["name"] = j.get('data', {}).get('uinfo', {}).get('base', {}).get('name', '')
                        msg["content"] = j.get('data', {}).get('message', '')
                        msg["price"] = j.get('data', {}).get('price', 0)
                        msg["color"] = j.get('data', {}).get('background_color', 'ffffff')
                        try:
                            msg['timestamp'] = j.get('data', {}).get('ts')
                        except:
                            msg['timestamp'] = datetime.now().timestamp()  # 如果没有时间戳，则使用当前时间
                        msg = SuperChatDanmaku(**msg)  # 转换为 SuperChatDanmaku 对象

                    elif msg["msg_type"] == "gift":
                        data = j.get('data', {})

                        # 1. 提取昵称 (优先取完整名称)
                        uname = data.get('sender_uinfo', {}).get('base', {}).get('name') or data.get('uname', '未知用户')

                        # 2. 提取礼物基本信息
                        gift_name = data.get('giftName', '未知礼物')
                        gift_num = data.get('num', 0)

                        # 3. 计算价值
                        # raw_price 为金瓜子单价
                        raw_price = data.get('price', 0)
                        # B站 1000金瓜子=1元；电池 1电池=0.1元 -> 所以 1电池 = 100金瓜子
                        gift_price_battery = raw_price / 100
                        # 总价值（金瓜子）
                        total_coin = data.get('total_coin') or (raw_price * gift_num) or 0
                        # 总价值（元）
                        total_price_cny = total_coin / 1000


                        # 时间戳处理
                        ts = data.get('timestamp') or data.get('ts') or datetime.now().timestamp()

                        text=f"<{uname}>送给主播价值{gift_price_battery:.0f}电池的{gift_name}x{gift_num}"

                        msg = GiftDanmaku(
                            timestamp=ts,
                            uname=uname,
                            content=text,
                            text=text,
                            gift_name=gift_name,
                            gift_count=gift_num,
                            gift_price=f"{gift_price_battery:.0f}",
                            price_unit='电池',
                            dtype='gift',
                            color='d9a6c4',
                            total_price_cny=total_price_cny,
                        )


                    else:
                        msg["content"] = j
                else:
                    msg = {"name": "", "content": dm.get('body'), "msg_type": "other"}

                msgs.append(msg)

            except Exception as e:
                # traceback.print_exc()
                # print("出错了")
                # print(e)
                pass

        return msgs
