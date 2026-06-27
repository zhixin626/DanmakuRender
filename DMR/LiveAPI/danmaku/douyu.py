import json, os, re, requests
from struct import pack, unpack
from datetime import datetime
from DMR.utils import BaseDanmaku, UserDanmaku, GiftDanmaku, MemberDanmaku
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

# --- 弹幕原始消息采样（用于反推协议格式：开通会员消息 + 其他未识别类型消息） ---
_sample_counter = {}
_sample_limit = 10
_sample_dir = "douyu_unknown_samples"

def sample_raw_message(msg_type, msg):
    """对指定 msg_type 各采样最多 _sample_limit 条，落盘成按类型分类的 jsonl
    返回 True 表示本次成功写入了一条新样本，False 表示已达到上限/写入失败"""
    cnt = _sample_counter.get(msg_type, 0)
    if cnt >= _sample_limit:
        return False
    _sample_counter[msg_type] = cnt + 1
    try:
        os.makedirs(_sample_dir, exist_ok=True)
        path = os.path.join(_sample_dir, f"{msg_type}.jsonl")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")
        return True
    except Exception as e:
        logger.info(f"{RED}【SAMPLE】{RESET}采样原始弹幕消息失败: {e}")
        return False


RED = "\033[31m"
YELLOW = "\033[33m"
GREEN = "\033[32m"
BLUE = "\033[34m"
RESET = "\033[0m"


def parse_stt(data: bytes) -> list:
    """斗鱼 STT(键@=值/) 文本协议的原始字节 -> dict 列表（每个 dict 即 decode_msg 里的 j）。
    一坨字节里常用 \\x00 粘多条消息：逐条把 STT 转成 json 文本(@=→":"、/→","，并反转义 @A/@S)
    再 json.loads。单条解析失败就跳过。"""
    out = []
    for seg in re.findall(b"(type@=.*?)\x00", data):
        try:
            seg = seg.replace(b"@=", b'":"').replace(b"/", b'","')
            seg = seg.replace(b"@A", b"@").replace(b"@S", b"/")
            out.append(json.loads((b'{"' + seg[:-2] + b"}").decode("utf8", "ignore")))
        except Exception:
            continue
    return out


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
        for j in parse_stt(data):    # j = 解析出的一条消息(dict)；斗鱼字段平铺在 j 里(无嵌套)
            try:
                msg_type = {
                    "chatmsg": "danmaku",
                    "dgb": "gift",
                    "uenter": "enter",
                    "dfrbc": "member",   # 开通/续费钻石粉丝(房间内带价格)；odfpbc/rdfpbc 是无价广播副本，不解析以免重复计费
                }.get(j["type"], "other")

                if msg_type == "danmaku":
                    msgs.append(UserDanmaku(
                        dtype="danmaku",
                        uname=j.get("nn", ""),
                        content=j.get("txt", ""),
                        text=j.get("txt", ""),
                        timestamp=datetime.now().timestamp(),
                        color=color_tab.get(j.get("col", "-1"), "ffffff"),
                        uid=j.get("uid", 0),
                        raw=j,
                    ))

                elif msg_type == "gift":
                    uname      = j.get("nn", "")
                    uid        = j.get("uid", "")
                    gift_name  = j.get("gfn", "未知礼物")
                    gift_count = int(j.get("gfcnt") or 1)
                    gfid       = int(j.get("gfid") or 0)   # 礼物 id
                    pid        = j.get("pid")              # 道具 id
                    price_unit = "鱼翅"
                    streamer   = j.get("receive_nn", "主播")

                    # 查价格：先按礼物 id，再按道具 id
                    gift_price, price_src, res = None, None, None
                    if gfid:
                        res, price_src = get_douyu_gift_info(gfid), "gift"
                    if not res and pid:
                        res, price_src = get_douyu_prop_info(pid), "prop"
                    if res:
                        gift_price = res.get("price")

                    # 判类型
                    if gift_price is None:
                        total_price_cny, extra = None, ""
                    elif gift_price == 0:
                        total_price_cny, extra = 0, ""
                    else:
                        total_price_cny, extra = gift_price * gift_count, f"价值{gift_price}{price_unit}的"

                    text = (f"送出{gift_name}({gift_price:g}{price_unit})x{gift_count}"
                            if gift_price is not None else f"送出{gift_name}x{gift_count}")
                    msgs.append(GiftDanmaku(
                        timestamp=datetime.now().timestamp(),
                        uname=uname,
                        uid=uid,
                        content=text,
                        text=text,
                        gift_name=gift_name,
                        gift_count=gift_count,
                        gift_price=gift_price,
                        gift_type=price_src,
                        price_unit=price_unit,
                        color='ffffff',
                        price_cny=total_price_cny,
                        gfid=gfid,
                        pid=pid,
                        raw=j,
                        extra=extra,
                    ))

                elif msg_type == "member":
                    # 钻石粉丝(dfrbc)：nick=开通者, rnick=主播, bn=粉丝牌名, mn=月数,
                    # cdays=累计天数, price=价格(单位「分」, 15800=158元)
                    uname    = j.get("nick", "")
                    streamer = j.get("rnick", "主播")
                    months   = int(j.get("mn") or 1)
                    price_cny = (int(j.get("price") or 0)) / 100
                    text = f"开通钻石粉丝({price_cny:g}元)×{months}月"
                    msgs.append(MemberDanmaku(
                        timestamp=datetime.now().timestamp(),
                        uname=uname,
                        uid=j.get("uid", ""),
                        content=text,
                        text=text,
                        member_name="钻石粉丝",
                        member_price=price_cny,
                        member_count=months,
                        member_unit="月",
                        price_cny=price_cny,
                        raw=j,
                    ))

                elif msg_type == "enter":
                    pass   # 进场暂不入列

                else:  # 理解不了的：采样 + 原样塞进 BaseDanmaku.raw，content 放 j 便于 --all 查看
                    raw_type = j.get("type", "unknown")
                    sample_raw_message(raw_type, j)
                    msgs.append(BaseDanmaku(
                        dtype=raw_type,
                        raw=j,
                        timestamp=datetime.now().timestamp(),
                        content=j,
                    ))

            except Exception as e:
                logger.debug(f"错误信息:{e}")
        return msgs
