import json, re,requests
from struct import pack, unpack
from datetime import datetime
from DMR.utils import  SimpleDanmaku,GiftDanmaku
import aiohttp
from DMR.utils import split_url
from .DMAPI import DMAPI
import logging
logger = logging.getLogger(__name__)
# RGB Color
color_tab = {
    "2": "80e5ff", # '1e87f0' to '00ccff' light blue (lv.6)
    "3": "b3ff80", # '7ac84b' to '66ff00' light green(teal) (lv.9)
    "4": "ffc299", # 'ff7f00' to 'ff6600' orange (lv.15)
    "6": "f985ac", # 'ff69b4' to 'f6447f' pink (lv.12)
    "5": "e580ff", # '9b39f4' to 'cc00ff' purple (lv.18)
    "1": "ff8080", # 'ff0000' to 'ff2e2e' red (lv.21)
}

def get_douyu_prop_info(pid: int):
    """
    根据道具ID (pid) 获取信息
    逻辑：如果是贵重道具(is_valuable=1)则返回鱼翅价格，否则返回0
    """
    url = "https://gift.douyucdn.cn/api/prop/v5/web/single"
    params = {"pid": pid}
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.douyu.com/"}
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        item = data.get("data", {})
        is_valuable = item.get("isValuable", 0)
        raw_price = item.get("price", 0)
        if is_valuable == 1:
            price_in_yuchi = raw_price / 100
        else:
            price_in_yuchi = 0
        return {
            "name": item.get("name"),
            "price": price_in_yuchi,      # 最终输出的价格
            "is_valuable": is_valuable,
            "pid": item.get("id")
        }
    except Exception as e:
        return None

def get_douyu_gift_info(gid: int):
    """根据礼物ID (gid) 获取信息"""
    url = "https://gift.douyucdn.cn/api/gift/v5/web/single"
    params = {"gid": gid, "skinId": 0}
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.douyu.com/"}
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        gift = data["data"]["giftList"][0]
        return {
            "name": gift.get("name"),
            "price": gift.get("priceInfo", {}).get("price")/100,
            "gid": gift.get("id")
        }
    except Exception as e:
        return None

RED = "\033[31m"
YELLOW = "\033[33m"
GREEN = "\033[32m"
BLUE = "\033[34m"
RESET = "\033[0m"

class Douyu(DMAPI):
    heartbeat = b"\x14\x00\x00\x00\x14\x00\x00\x00\xb1\x02\x00\x00\x74\x79\x70\x65\x40\x3d\x6d\x72\x6b\x6c\x2f\x00"

    async def get_ws_info(url, **kwargs):
        reg_datas = []
        _, room_id = split_url(url)
        async with aiohttp.ClientSession() as session:
            async with session.get('https://m.douyu.com/' + str(room_id)) as resp:
                room_page = await resp.text()
                room_id = re.findall(r'rid":(\d*),"vipId', room_page)[0]

        data = f"type@=loginreq/roomid@={room_id}/"
        s = pack("i", 9 + len(data)) * 2
        s += b"\xb1\x02\x00\x00"  # 689
        s += data.encode("ascii") + b"\x00"
        reg_datas.append(s)
        data = f"type@=joingroup/rid@={room_id}/gid@=-9999/"
        s = pack("i", 9 + len(data)) * 2
        s += b"\xb1\x02\x00\x00"  # 689
        s += data.encode("ascii") + b"\x00"
        reg_datas.append(s)
        return "wss://danmuproxy.douyu.com:8506/", reg_datas

    def decode_msg(data):
        msgs = []
        for msg in re.findall(b"(type@=.*?)\x00", data):
            try:
                msg = msg.replace(b"@=", b'":"').replace(b"/", b'","')
                msg = msg.replace(b"@A", b"@").replace(b"@S", b"/")
                msg = json.loads((b'{"' + msg[:-2] + b"}").decode("utf8", "ignore"))

                uname    = msg.get("nn", "")
                content  = msg.get("txt", "")
                msg_type = {"dgb": "gift", "chatmsg": "danmaku", "uenter": "enter"}.get(msg["type"], "other")
                uid      = msg["uid"]
                color    = color_tab.get(msg.get("col", "-1"), "ffffff")

                if msg_type == "gift":
                    # ===== 0. 基础字段 =====
                    gift_name  = msg.get("gfn", "未知礼物")
                    gift_count = int(msg.get("gfcnt") or 1)
                    gfid       = int(msg.get("gfid") or 0)   # 礼物 id
                    pid        = msg.get("pid")              # 道具 id
                    gpf        = msg.get("gpf")              # 白嫖标志
                    price_unit = "鱼翅"
                    streamer   = msg.get("receive_nn", "主播")

                    gift_price = None
                    price_src  = None   # gift / prop
                    res        = None

                    # ===== 1. 查价格 =====
                    if gfid:
                        res = get_douyu_gift_info(gfid)
                        price_src = "gift"

                    if not res and pid:
                        res = get_douyu_prop_info(pid)
                        price_src = "prop"

                    if res:
                        gift_price = res.get("price")

                    # ===== 2. 判类型 =====
                    if gift_price is None:
                        douyu_type = "unknown"
                        total_price_cny = None
                        extra = ""
                    elif gift_price == 0:
                        douyu_type = "free"
                        total_price_cny = 0
                        extra = ""
                    else:
                        douyu_type = "paid"
                        total_price_cny = gift_price * gift_count
                        extra = f"价值{gift_price}{price_unit}的"

                    # ===== 3. 文本 =====
                    text = f"{uname} 送给{streamer}{extra}{gift_name}×{gift_count}"

                    # ================== DEBUG ==================
                    if douyu_type == "paid":
                        pass
                        # print(f"{YELLOW}【PAID】{RESET}{text}")
                        # print(
                        #     f"src={price_src} gfid={gfid} pid={pid} "
                        #     f"gift_price={gift_price} count={gift_count} "
                        #     f"total_price_cny={total_price_cny} gpf={gpf} ct={msg.get('ct')}"
                        # )

                    elif douyu_type == "free":
                        pass
                        # print(f"【FREE】{text}")
                        # print(
                        #     f"src={price_src} gfid={gfid} pid={pid} "
                        #     f"gift_price={gift_price} count={gift_count} "
                        #     f"total_price_cny={total_price_cny} gpf={gpf} ct={msg.get('ct')}"
                        # )

                    else:  # unknown
                        print(f"{RED}【UNKNOWN】{RESET}{text}")
                        print(
                            f"src={price_src} gfid={gfid} pid={pid} "
                            f"gift_price={gift_price} count={gift_count} "
                            f"total_price_cny={total_price_cny} gpf={gpf} ct={msg.get('ct')}"
                        )
                        print(msg)
                    # ================== DEBUG ==================

                    gift_msg_obj = GiftDanmaku(
                        timestamp=datetime.now().timestamp(),
                        uname=uname,
                        content=text,
                        text=text,
                        gift_name=gift_name,
                        gift_count=gift_count,
                        gift_price=gift_price,
                        price_unit=price_unit,
                        dtype='gift',
                        color='ffffff',
                        total_price_cny=total_price_cny,
                        gfid=gfid,
                        pid=pid,
                        raw_data=msg,
                        extra=extra,
                    )
                    msgs.append(gift_msg_obj)
                    continue

                elif msg_type == "danmaku":
                    msg = SimpleDanmaku(
                            dtype="danmaku",
                            uname=uname,
                            content=content,
                            timestamp=datetime.now().timestamp(),
                            color=color,
                            uid=uid,
                        )
                    msgs.append(msg)

            except Exception as e:
                logger.debug(f"错误信息:{e}")
                pass
        return msgs
