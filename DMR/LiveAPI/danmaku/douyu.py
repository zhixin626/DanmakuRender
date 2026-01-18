import json, re, select, random
from struct import pack, unpack
from datetime import datetime
from DMR.utils import  SimpleDanmaku,GiftDanmaku
import aiohttp
from DMR.utils import split_url
from .DMAPI import DMAPI

# RGB Color
color_tab = {
    "2": "80e5ff", # '1e87f0' to '00ccff' light blue (lv.6)
    "3": "b3ff80", # '7ac84b' to '66ff00' light green(teal) (lv.9)
    "4": "ffc299", # 'ff7f00' to 'ff6600' orange (lv.15)
    "6": "f985ac", # 'ff69b4' to 'f6447f' pink (lv.12)
    "5": "e580ff", # '9b39f4' to 'cc00ff' purple (lv.18)
    "1": "ff8080", # 'ff0000' to 'ff2e2e' red (lv.21)
}


DOUYU_GIFT_VALUE = {
    # 基础礼物 (Image 1 & 2)
    "赞": 0.1, "弱鸡": 0.2, "仙女棒": 1, "办卡": 6, "青鸾化仙": 2000, "飞机": 100,
    "火箭": 500, "探险超火": 2000, "探险飞机": 100, "超级火箭": 2000, "宇宙飞船": 5000,
    "钻粉卡": 6, "摇滚小熊": 20, "音效飞机": 100, "带宽券": 0.1, "弱鸡拳击": 100, "鲨鲨喷漆": 100,
    "粉丝卡": 6, "高能弹幕": 10, "惊喜盒子": 0.5, "破空飞机": 100, "星际卡": 6, "梦": 666,

    # 至尊/浪漫系列 (Image 3 & 4)
    "星空丘比特": 1314, "至尊飞机": 100, "至尊火箭": 500, "至尊超火": 2000, "心愿纸鹤": 1, "小星星": 9.9,
    "守卫权杖": 10, "至尊飞船": 5000, "爱神丘比特": 66, "幸福卡": 6, "爱意信封": 18.8, "浪漫纸鹤": 30,
    "告白气球": 520, "浪漫烟花": 9.9, "流星雨": 66, "天宫玉阙": 2000, "陪伴飞机": 100, "浪漫约会": 1314,
    "星梦飞机": 100, "城堡气球": 2000, "挚爱之心": 3000, "粉丝灯牌": 6, "潘多拉魔盒": 1, "告白卡": 6,

    # 趣味/特殊 (Image 5 & 6)
    "超神": 0.1, "下饭": 0.1, "星际飞车": 50, "KPL加油": 1, "为爱发电": 5, "钻石": 1, "水晶塔": 0.2,
    "老司机": 6, "牛啤": 6, "小心心": 0.1, "GG": 0.1, "炒CP": 50, "挚爱之吻": 1000,
    "斗鱼666号": 1000, "全力守护": 0.1, "爱的CD": 1, "怦然心动": 6, "浪漫旅行车": 66, "童话马车": 166,
}
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
                    gift_name = msg.get("gfn", "未知礼物")
                    gift_num = int(msg.get("gfcnt", 1))

                    # 使用 None 来区分“未录入”和“价值为0”
                    yuchi_value = DOUYU_GIFT_VALUE.get(gift_name)

                    if yuchi_value is not None:
                        # 已知礼物：计算总价值并应用 0.1 元门槛
                        total_yuchi = yuchi_value * gift_num
                        if total_yuchi >= 0.1:
                            text = f"<{uname}>送给主播价值{yuchi_value:.1f}鱼翅的{gift_name}x{gift_num}"
                            gift_price = f"{yuchi_value:.1f}"
                        else:
                            continue # 明确知道价值但太低的礼物，跳过
                    else:
                        # 未知礼物：由于不确定价值，为了保险起见，全部显示
                        text = f"<{uname}>送给主播{gift_name}x{gift_num}"
                        gift_price = "0"

                    # 统一构造对象
                    gift_msg_obj = GiftDanmaku(
                        timestamp=datetime.now().timestamp(),
                        uname=uname,
                        content=text,
                        text=text,
                        gift_name=gift_name,
                        gift_count=gift_num,
                        gift_price=gift_price,
                        price_unit='鱼翅',
                        dtype='gift',
                        color='ffffff',
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
                pass
        return msgs
