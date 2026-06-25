from datetime import datetime
from typing import Union


def format_price(v):
    """格式化价格：保留最多 1 位小数并去掉多余的 0。
    1.0->1, 1.2->1.2, 1.20->1.2, 1.234->1.2；None 原样返回。"""
    if v is None:
        return None
    try:
        f = round(float(v), 1)
    except (TypeError, ValueError):
        return v
    return int(f) if f == int(f) else f


class SimpleDanmaku():
    def __init__(self,
                 time:float=None,
                 timestamp:float=None,
                 dtype:str=None,
                 uname:str=None,
                 color:str='ffffff',
                 content:str=None,
                 text:str=None,
                 uid:str=None,
                 is_vip:bool=False,
                 **kwargs,
                 ) -> None:
        # time 表示相对时间，单位为秒
        # timestamp 表示绝对时间，单位为秒
        self.time = time
        if isinstance(timestamp, datetime):
            self.timestamp = timestamp.timestamp()
        elif timestamp is None:
            self.timestamp = datetime.now().timestamp()
        else:
            self.timestamp = float(timestamp)

        self.dtype   = dtype      # 弹幕类型，未知类型需要设置为 'other'
        self.uname   = uname      # 发送者名称
        self.color   = color      # 弹幕颜色，6位16进制颜色码
        self.content = content  # 弹幕内容，可能是纯文本或其他格式
        self.uid     = uid
        self.is_vip  = is_vip

        for key, value in kwargs.items(): # dm.uname 将 kwargs 中的任意键值对动态添加为对象属性
            self.__dict__[key] = value

        # text属性表示最终显示的字符串
        self.text = text if text is not None else self.content

    def __getitem__(self, key): # dm["uname"] 这是让你的对象可以像字典一样用中括号访问字段
        return self.__dict__[key]

    def __iter__(self): # 可以遍历 for k, v in dm: print(k, v)
        for key, value in self.__dict__.items():
            yield key, value

class MemberDanmaku(SimpleDanmaku):
    def __init__(
        self,
        uname:str,
        price:float,
        member_name:str,      #"会员"
        price_unit:str,       #"元"
        member_time:int,      # 1
        member_time_unit:str, #"月"
        text=None,
        dtype="member",
        **kwargs,
    ):
        super().__init__(uname=uname,dtype=dtype,**kwargs)
        self.dtype            = 'member'
        self.member_name      = member_name
        self.member_time      = member_time
        self.member_time_unit = member_time_unit
        self.price            = format_price(price)
        if text:
            self.text=text
        else:
            self.text=f"{uname} 开通了{member_time}个{member_time_unit}的{member_name}价值{self.price}{price_unit}"

        self.gift_coverter_content=f"开通{member_name}({self.price}{price_unit})x{member_time}{member_time_unit}"

class GiftDanmaku(SimpleDanmaku):
    def __init__(
        self,
        text: str = None,
        price: float = None,
        gift_name: str = '',
        gift_count: int = 1,
        gift_price: float = 0.0,
        price_unit: str = '',
        total_price_cny: Union[float,None] = None,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.dtype           = 'gift'
        self.gift_name       = gift_name
        self.gift_count      = int(gift_count)
        self.price_unit      = price_unit
        self.gift_price      = format_price(gift_price if gift_price is not None else 0.0)
        self.total_price_cny = format_price(total_price_cny)
        self.price           = format_price(price if price is not None else self.gift_price * self.gift_count)

        if text:
            self.text = text
        else:
            self.text =f'{self.uname} 送给主播价值{self.gift_price}{self.price_unit}的{self.gift_name}x{self.gift_count}'

        self.gift_coverter_content=f"送出{self.gift_name}({self.gift_price}{self.price_unit})x{self.gift_count}"

class SuperChatDanmaku(SimpleDanmaku):
    def __init__(
        self,
        price: float = 0.0,
        price_unit: str = 'CNY',
        price_cny: float = 0.0,   # 真实元价格，用于统计收益
        sc_duration: int = 0,
        name: str = '',
        background_color="FFF5ED",
        background_bottom_color="B2602A",
        name_color="000000",
        content_color="FFFFFF",
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.dtype = 'superchat'
        self.uname = name
        self.price = price
        self.price_unit = price_unit
        self.price_cny = float(price_cny)   # 真实元价格
        self.sc_duration = sc_duration
        self.background_color=background_color
        self.background_bottom_color=background_bottom_color
        self.name_color=name_color
        self.content_color=content_color


class EntryDanmaku(SimpleDanmaku):
    def __init__(
        self,
        text:str=None,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.dtype = 'entry'
        self.text = text if text is not None else f'{self.uname} 进入直播间'
