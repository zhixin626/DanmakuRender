# 抖音的弹幕录制参考了 https://github.com/LyzenX/DouyinLiveRecorder 和 https://github.com/YunzhiYike/live-tool
# 抖音的弹幕录制参考了 https://github.com/biliup/biliup/blob/master/biliup/plugins/Danmaku/douyin.py
# 2024.6.23 抖音的弹幕录制参考了 https://github.com/SecPhases/DanmakuRender/commit/fd6d85afede5845274ad699bbcdf5db98e68977e

from datetime import datetime
import base64
import os
import threading
import asyncio
import gzip
import re
import time
import re
import requests
import urllib
import json
import logging
import random
import websocket
from google.protobuf import json_format
from concurrent.futures import ThreadPoolExecutor, as_completed

from DMR.LiveAPI.douyin import douyin_utils
from DMR.utils import split_url, cookiestr2dict, SimpleDanmaku, GiftDanmaku, EntryDanmaku
from .dy_pb2 import PushFrame, Response, ChatMessage, GiftMessage, MemberMessage
from .utils import DouyinDanmakuUtils
import aiohttp

from DMR.utils.bark_notifier  import bark_notify

logger = logging.getLogger(__name__)

RED = "\033[31m"
YELLOW = "\033[33m"
GREEN = "\033[32m"
BLUE = "\033[34m"
RESET = "\033[0m"

def _decode_protobuf_raw(data: bytes, max_depth: int = 4):
    """
    在没有 .proto 定义的情况下，按 protobuf wire format 通用解码（类似 protoc --decode_raw）。
    返回 {field_number: [values...]}，遇到长度分隔字段(wire type 2)会尝试递归当作嵌套消息解析，
    解析失败则退化为字符串/原始字节，方便人工查看反推协议结构。
    """
    result = {}
    i = 0
    n = len(data)
    while i < n:
        # 解析 tag（varint）
        tag = 0
        shift = 0
        start = i
        while True:
            if i >= n:
                return result
            b = data[i]
            i += 1
            tag |= (b & 0x7F) << shift
            if not (b & 0x80):
                break
            shift += 7
        field_number = tag >> 3
        wire_type = tag & 0x07

        if wire_type == 0:  # varint
            value = 0
            shift = 0
            while True:
                if i >= n:
                    return result
                b = data[i]
                i += 1
                value |= (b & 0x7F) << shift
                if not (b & 0x80):
                    break
                shift += 7
        elif wire_type == 1:  # 64-bit
            if i + 8 > n:
                return result
            raw = data[i:i+8]
            value = {
                "hex": raw.hex(),
                "as_uint64_le": int.from_bytes(raw, "little"),
            }
            i += 8
        elif wire_type == 2:  # length-delimited
            length = 0
            shift = 0
            while True:
                if i >= n:
                    return result
                b = data[i]
                i += 1
                length |= (b & 0x7F) << shift
                if not (b & 0x80):
                    break
                shift += 7
            if i + length > n:
                return result
            raw = data[i:i+length]
            i += length
            if max_depth > 0:
                try:
                    nested = _decode_protobuf_raw(raw, max_depth=max_depth - 1)
                    if nested:
                        value = nested
                    else:
                        raise ValueError
                except Exception:
                    try:
                        value = raw.decode('utf-8')
                    except Exception:
                        value = base64.b64encode(raw).decode('ascii')
            else:
                try:
                    value = raw.decode('utf-8')
                except Exception:
                    value = base64.b64encode(raw).decode('ascii')
        elif wire_type == 5:  # 32-bit
            if i + 4 > n:
                return result
            raw = data[i:i+4]
            value = {
                "hex": raw.hex(),
                "as_uint32_le": int.from_bytes(raw, "little"),
            }
            i += 4
        else:
            # 未知 wire type，停止解析，返回已解出的部分
            return result

        result.setdefault(str(field_number), []).append(value)
    return result


class Douyin:
    heartbeat = b':\x02hb'
    heartbeatInterval = 10

    # --- 未识别消息采样（用于反推协议格式，如开通会员消息） ---
    _unknown_sample_counter = {}
    _unknown_sample_limit = 10
    _unknown_sample_dir = "douyin_unknown_samples"

    @classmethod
    def _sample_unknown_message(cls, msg):
        """对未识别的 method 各采样最多 _unknown_sample_limit 条，落盘成按 method 分类的 jsonl"""
        method = msg.method
        cnt = cls._unknown_sample_counter.get(method, 0)
        if cnt >= cls._unknown_sample_limit:
            return
        cls._unknown_sample_counter[method] = cnt + 1
        try:
            os.makedirs(cls._unknown_sample_dir, exist_ok=True)
            record = {
                "method": method,
                "msgId": msg.msgId,
                "decoded": _decode_protobuf_raw(msg.payload),
            }
            path = os.path.join(cls._unknown_sample_dir, f"{method}.jsonl")
            with open(path, "a", encoding="utf-8") as f:
                # indent=2 方便人工查看/直接复制粘贴发给我分析；用 --- 分隔每条样本
                f.write(json.dumps(record, ensure_ascii=False, indent=2) + "\n---\n")
        except Exception as e:
            logger.info(f"{RED}【SAMPLE】{RESET}采样未识别弹幕消息失败: {e}")

    def __init__(self, douyin_dm_cookies:str=None) -> None:
        if not douyin_dm_cookies:
            logger.info(f"[*] 正在使用 游客身份 获取抖音弹幕")
            self.headers = douyin_utils.get_headers()
        else:
            try:
                if douyin_dm_cookies.endswith('.json'):
                    with open(douyin_dm_cookies, 'r', encoding='utf-8') as f:
                        cookies = json.load(f)
                else:
                    cookies = cookiestr2dict(douyin_dm_cookies)
                self.headers = douyin_utils.get_headers(extra_cookies=cookies)
                logger.info(f"[*] 正在使用 {douyin_dm_cookies} 的cookies获取抖音弹幕")
            except Exception as e:
                logger.exception(f'解析抖音cookies错误: {e}, 使用默认cookies.')
                self.headers = douyin_utils.get_headers()

    async def get_ws_info(self, url, **kwargs):
        async with aiohttp.ClientSession() as session:
            _, room_id = split_url(url)
            async with session.get(
                    douyin_utils.build_request_url(f"https://live.douyin.com/webcast/room/web/enter/?web_rid={room_id}"),
                    headers=self.headers, timeout=5) as resp:
                room_info = json.loads(await resp.text())['data']['data'][0]
                USER_UNIQUE_ID = DouyinDanmakuUtils.get_user_unique_id()
                VERSION_CODE = 180800 # https://lf-cdn-tos.bytescm.com/obj/static/webcast/douyin_live/7697.782665f8.js -> a.ry
                WEBCAST_SDK_VERSION = "1.0.14-beta.0" # https://lf-cdn-tos.bytescm.com/obj/static/webcast/douyin_live/7697.782665f8.js -> ee.VERSION
                # logger.info(f"user_unique_id: {USER_UNIQUE_ID}")
                sig_params = {
                    "live_id": "1",
                    "aid": "6383",
                    "version_code": VERSION_CODE,
                    "webcast_sdk_version": WEBCAST_SDK_VERSION,
                    "room_id": room_info['id_str'],
                    "sub_room_id": "",
                    "sub_channel_id": "",
                    "did_rule": "3",
                    "user_unique_id": USER_UNIQUE_ID,
                    "device_platform": "web",
                    "device_type": "",
                    "ac": "",
                    "identity": "audience"
                }
                try:
                    signature = DouyinDanmakuUtils.get_signature(DouyinDanmakuUtils.get_x_ms_stub(sig_params))
                except Exception as e:
                    signature = 0
                    logger.exception('获取抖音弹幕签名失败:')
                    logger.exception(e)
                # logger.info(f"signature: {signature}")
                webcast5_params = {
                    "room_id": room_info['id_str'],
                    "compress": 'gzip',
                    # "app_name": "douyin_web",
                    "version_code": VERSION_CODE,
                    "webcast_sdk_version": WEBCAST_SDK_VERSION,
                    # "update_version_code": "1.0.14-beta.0",
                    # "cookie_enabled": "true",
                    # "screen_width": "1920",
                    # "screen_height": "1080",
                    # "browser_online": "true",
                    # "tz_name": "Asia/Shanghai",
                    # "cursor": "t-1718899404570_r-1_d-1_u-1_h-7382616636258522175",
                    # "internal_ext": "internal_src:dim|wss_push_room_id:7382580251462732598|wss_push_did:7344670681018189347|first_req_ms:1718899404493|fetch_time:1718899404570|seq:1|wss_info:0-1718899404570-0-0|wrds_v:7382616716703957597",
                    # "host": "https://live.douyin.com",
                    "live_id": "1",
                    "did_rule": "3",
                    # "endpoint": "live_pc",
                    # "support_wrds": "1",
                    "user_unique_id": USER_UNIQUE_ID,
                    # "im_path": "/webcast/im/fetch/",
                    "identity": "audience",
                    # "need_persist_msg_count": "15",
                    # "insert_task_id": "",
                    # "live_reason": "",
                    # "heartbeatDuration": "0",
                    "signature": signature,
                }
                wss_url = f"wss://webcast5-ws-web-lf.douyin.com/webcast/im/push/v2/?{'&'.join([f'{k}={v}' for k, v in webcast5_params.items()])}"
                url = douyin_utils.build_request_url(wss_url)
                return url, []

    @classmethod
    def decode_msg(cls, data):
        wss_package = PushFrame()
        wss_package.ParseFromString(data)
        log_id = wss_package.logId
        # 抖音的 payload 不一定 gzip 压缩:只有以 gzip 魔数(\x1f\x8b)开头才解压,
        # 否则是未压缩的 Response protobuf,直接解析(否则 gzip.decompress 会抛 BadGzipFile)。
        payload = wss_package.payload
        if payload[:2] == b'\x1f\x8b':
            payload = gzip.decompress(payload)
        payload_package = Response()
        payload_package.ParseFromString(payload)

        ack = None
        if payload_package.needAck:
            obj = PushFrame()
            obj.payloadType = 'ack'
            obj.logId = log_id
            obj.payloadType = payload_package.internalExt
            ack = obj.SerializeToString()
        
        msgs = []
        for msg in payload_package.messagesList:
            now = datetime.now().timestamp()
            # print(msg)
            # if msg.method == 'WebcastSocialMessage':
            #     chatMessage = ChatMessage()
            #     chatMessage.ParseFromString(msg.payload)
            #     data        = json_format.MessageToDict(chatMessage, preserving_proto_field_name=True)
            #     print(data)
            if msg.method == 'WebcastChatMessage':
                chatMessage = ChatMessage()
                chatMessage.ParseFromString(msg.payload)
                data        = json_format.MessageToDict(chatMessage, preserving_proto_field_name=True)
                # print(data)
                
                user_info = data.get('user', {})
                # print(user_info)
                name     = user_info.get('nickName') or "未知用户"
                uid      = user_info.get('shortId',None)
                content  = data['content']
                msg_dict = SimpleDanmaku(
                    timestamp=now,
                    uname=name,
                    content=content,   # 保留原始 [xxx],交给渲染引擎用表情包替换(不再转 unicode)
                    dtype='danmaku',
                    color='ffffff',
                    uid=uid,
                )
                
                # logger.info("WebcastChatMessage\n%s", json.dumps(data, ensure_ascii=False, indent=2))

                
                # msg_dict = {"timestamp": now, "name": name, "content": content, "msg_type": "danmaku", "color": "ffffff"}
                # print(msg_dict)
            elif msg.method == 'WebcastMemberMessage':
                memberMessage = MemberMessage()
                memberMessage.ParseFromString(msg.payload)
                data = json_format.MessageToDict(memberMessage, preserving_proto_field_name=True)
                
                user_info = data.get('user', {})
                name      = user_info.get('nickName') or "未知用户"
                uid       = user_info.get('id',None)
                # 抖音号叫做 unique_id 是用户可以改的
                # short_id 才是真的不能改的
                # id 是长id也是不能改的
                if "学会自己爬" in name:
                    print(f"👋 {name} 进入直播间！ uid is {uid}")
                    print(user_info)
                    print(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                    print(f"目标id is 55557889854")
                    bark_notify("抖音通知", f"{name} 进入直播间！")

                msg_dict = EntryDanmaku(
                    timestamp = now,
                    uname     = name,
                    content   = f"{name}来了",
                    dtype     = 'entry',
                    color     = 'ffffff',
                    uid       = uid,
                )

            elif msg.method == 'WebcastGiftMessage':
                giftMessage = GiftMessage()
                giftMessage.ParseFromString(msg.payload)
                data        = json_format.MessageToDict(giftMessage, preserving_proto_field_name=True)
                if 'combo' in data['gift'] and not 'repeatEnd' in data:
                  continue
                user_info   = data.get('user', {})
                name        = user_info.get('nickName') or user_info.get('shortId') or "未知用户"

                gift_price      = int(data['gift']['diamondCount'])
                gift_count      = int(data.get('repeatCount', 1))
                gift_name       = data['gift']['name']
                total_price     = gift_price*gift_count
                total_price_cny = total_price/10

                msg_dict = GiftDanmaku(
                    timestamp       = now,
                    uname           = name,
                    content         = f"{name} 送给主播价值{gift_price}抖币的{gift_name}×{gift_count}",
                    text            = f"{name} 送给主播价值{gift_price}抖币的{gift_name}×{gift_count}",
                    gift_name       = gift_name,
                    gift_count      = gift_count,
                    gift_price      = gift_price,
                    price_unit      = '抖币',
                    price           = total_price,
                    dtype           = 'gift',
                    color           = 'ffffff',
                    total_price_cny = total_price_cny,
                )

            else:
                cls._sample_unknown_message(msg)
                msg_dict = {"timestamp": now, "name": "", "content": "", "msg_type": "other", "raw_data": msg}

            msgs.append(msg_dict)

        return msgs, ack
