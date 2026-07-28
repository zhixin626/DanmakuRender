from datetime import datetime


def format_price(v):
    """格式化价格：用 :g 规则去掉无意义的尾零（198.0->198, 1.20->1.2, 1.234->1.234）。None 原样返回。"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    return int(f) if f == int(f) else float(f'{f:g}')


class BaseDanmaku():
    """所有流式消息的根：只持有任何消息都有的"信封"字段，并支持像字典一样取属性
    （dm['x'] / for k,v in dm）。不假设有发送者——无发送者的系统/未知消息也可直接是 BaseDanmaku，
    解析不了的原始内容统一塞在 raw 里。必传：dtype, raw, timestamp。"""
    def __init__(self, dtype, raw=None, timestamp=None, received_at=None, **kwargs):
        self.dtype = dtype
        self.raw   = raw
        timestamp = received_at if received_at is not None else timestamp
        if timestamp is None:
            self.timestamp = None
        elif isinstance(timestamp, datetime):
            self.timestamp = timestamp.timestamp()
        else:
            self.timestamp = float(timestamp)
        # received_at 是唯一用于新 JSONL 时间轴的字段。timestamp 暂留作旧 ASS
        # 兼容别名，平台 decoder 给出的值会在 DanmakuClient 入队前统一覆盖。
        self.received_at = self.timestamp
        self.time  = None
        for key, value in kwargs.items():   # 平台特有字段动态挂上
            self.__dict__[key] = value

    def __getitem__(self, key):
        return self.__dict__[key]

    def __iter__(self):
        for key, value in self.__dict__.items():
            yield key, value


class UserDanmaku(BaseDanmaku):
    """普通弹幕 / 表情弹幕。必传：uname, uid, color, content, text（+ 根的 dtype/raw/timestamp）。
    可选：face_url（头像）。"""
    def __init__(self, uname, uid, color, content, text, face_url=None, **kwargs):
        super().__init__(**kwargs)
        self.uname    = uname
        self.uid      = uid
        self.color    = color
        self.content  = content
        self.text     = text
        self.face_url = face_url


class EntryDanmaku(BaseDanmaku):
    """进场消息。必传 uname；dtype 固定 'entry'。"""
    def __init__(self, uname, uid=None, color='ffffff', content=None, text=None, face_url=None, **kwargs):
        super().__init__(dtype='entry', **kwargs)
        self.uname    = uname
        self.uid      = uid
        self.color    = color
        self.content  = content
        self.text     = text if text is not None else f'{uname} 进入直播间'
        self.face_url = face_url


class GiftDanmaku(BaseDanmaku):
    """礼物。dtype 固定 'gift'。
    必传：uname, uid, content, text, gift_name, gift_count, gift_price, price_unit, price_cny。"""
    def __init__(self, uname, uid, content, text,
                 gift_name, gift_count, gift_price, price_unit, price_cny,
                 unit_price_cny=None, color='ffffff', **kwargs):
        super().__init__(dtype='gift', **kwargs)
        self.uname      = uname
        self.uid        = uid
        self.color      = color
        self.content    = content
        self.text       = text
        self.gift_name  = gift_name
        self.gift_count = int(gift_count)
        self.gift_price = format_price(gift_price)   # 单个礼物平台币价
        self.price_unit = price_unit                 # 平台币单位(电池/鱼翅/抖币)
        self.unit_price_cny = format_price(unit_price_cny)  # 单个礼物人民币价
        self.price_cny  = format_price(price_cny)     # 人民币收益(元，总价)


class SuperChatDanmaku(BaseDanmaku):
    """醒目留言 SC。dtype 固定 'superchat'。无 count（一次一条）。
    必传：uname, uid, content, text, price(平台币价), sc_duration, price_cny。可选：颜色等。"""
    def __init__(self, uname, uid, content, text, price, sc_duration, price_cny,
                 price_unit='电池', color='ffffff',
                 background_color="FFF5ED", background_bottom_color="B2602A",
                 name_color="000000", content_color="FFFFFF", **kwargs):
        super().__init__(dtype='superchat', **kwargs)
        self.uname       = uname
        self.uid         = uid
        self.color       = color
        self.content     = content
        self.text        = text
        self.price       = format_price(price)        # 平台币价(如电池数)
        self.price_unit  = price_unit
        self.sc_duration = sc_duration
        self.price_cny   = format_price(price_cny)    # 人民币收益(元)
        self.background_color        = background_color
        self.background_bottom_color = background_bottom_color
        self.name_color    = name_color
        self.content_color = content_color


class MemberDanmaku(BaseDanmaku):
    """开通大航海/会员。dtype 固定 'member'。
    必传：uname, uid, content, text, member_name, member_price, member_count, member_unit, price_cny。
    （展示文案由渲染阶段的 LayoutCompiler 决定，不在此持有）"""
    def __init__(self, uname, uid, content, text,
                 member_name, member_price, member_count, member_unit, price_cny,
                 color='ffffff', **kwargs):
        super().__init__(dtype='member', **kwargs)
        self.uname        = uname
        self.uid          = uid
        self.color        = color
        self.content      = content
        self.text         = text
        self.member_name  = member_name
        self.member_price = format_price(member_price)   # 平台币/元价
        self.member_count = member_count                 # 开通数量(月数)
        self.member_unit  = member_unit                  # 单位(月)
        self.price_cny    = format_price(price_cny)       # 人民币收益(元)
