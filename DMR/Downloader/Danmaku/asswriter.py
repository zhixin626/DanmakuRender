from datetime import datetime
import threading
from DMR.utils import *
from itertools import cycle


__all__ = ['AssWriter']

def hex_opacity(opacity):
    return hex(255-int(opacity*255))[2:].zfill(2)
def darken_for_border(hex_color: str, lightness_target: float = 0.22) -> str:
    """
    将 hex_color (#RRGGBB) 转为同色系的深色，用作描边。
    lightness_target: HSL 中目标亮度，0.22 对应较深的阴影色。
    返回 BGR 格式字符串（供 ASS 使用）。
    """
    hex_color = hex_color.lstrip('#')
    r, g, b = (int(hex_color[i:i+2], 16) / 255.0 for i in (0, 2, 4))

    # RGB -> HSL
    cmax, cmin = max(r, g, b), min(r, g, b)
    delta = cmax - cmin
    l = (cmax + cmin) / 2

    if delta == 0:
        h, s = 0.0, 0.0
    else:
        s = delta / (1 - abs(2 * l - 1))
        if cmax == r:
            h = ((g - b) / delta) % 6
        elif cmax == g:
            h = (b - r) / delta + 2
        else:
            h = (r - g) / delta + 4
        h /= 6

    # 强制压低亮度，饱和度稍微提升以保持色感
    l_new = lightness_target
    s_new = min(s * 1.2, 1.0) if s > 0 else 0.0

    # HSL -> RGB
    c = (1 - abs(2 * l_new - 1)) * s_new
    x = c * (1 - abs((h * 6) % 2 - 1))
    m = l_new - c / 2

    h6 = h * 6
    if   h6 < 1: r2, g2, b2 = c, x, 0
    elif h6 < 2: r2, g2, b2 = x, c, 0
    elif h6 < 3: r2, g2, b2 = 0, c, x
    elif h6 < 4: r2, g2, b2 = 0, x, c
    elif h6 < 5: r2, g2, b2 = x, 0, c
    else:        r2, g2, b2 = c, 0, x

    r_out = int((r2 + m) * 255)
    g_out = int((g2 + m) * 255)
    b_out = int((b2 + m) * 255)

    return RGB2BGR(f'{r_out:02X}{g_out:02X}{b_out:02X}')
class AssWriter():
    """
    ASS弹幕写入器，定义了ASS弹幕格式和信息，用于流式处理弹幕
    """
    def __init__(self,
                 description:str,
                 width:int,
                 height:int,
                 dst:int,
                 dmrate:float,
                 font:str,
                 fontsize:int,
                 margin_h:int,
                 margin_w:int,
                 dmduration:float,
                 opacity:float,
                 auto_fontsize:bool,
                 outlinecolor:str,
                 outlinesize:int,
                 gift_dm_args:dict={},
                 **kwargs) -> None:
        self.gift_dm_args=gift_dm_args
        self.description = description
        self.height = height
        self.width = width
        self.dmrate = dmrate
        if auto_fontsize:
            self.fontsize = int(height / 1080 * fontsize)
        else:
            self.fontsize = int(fontsize)
        self.font = font

        self.margin_h = margin_h if margin_h > 1 else margin_h * self.height
        self.margin_w = margin_w if margin_w > 1 else margin_w * self.width
        self.dst = dst
        self.dmduration = dmduration
        self.opacity = hex_opacity(opacity)
        self.outlinecolor = str(outlinecolor).zfill(6)
        self.outlinesize = outlinesize
        self.kwargs = kwargs

        self._lock = threading.Lock()
        self._super_chat_tails = []  # 初始化 _super_chat_tails 属性
        self._super_chat_state = 0
        self._latest_end_time = 0
        self._latest_y = 100  # 新增，记录下一条SC的y坐标
        self._ntracks = int(((self.height - self.dst) * self.dmrate) / (self.fontsize + self.margin_h))

        # PIL 测宽器(字体回退链 + 标定 + 带上限缓存),替代旧的字节数估算
        self.measurer = TextMeasurer(self.font, self.fontsize, bold=True)

        self.meta_info = [
            '[Script Info]',
            f'Title: {self.description}',
            'ScriptType: v4.00+',
            'Collisions: Normal',
            f'PlayResX: {self.width}',
            f'PlayResY: {self.height}',
            'Timer: 100.0000',
            'WrapStyle: 2',
            '',
            '[V4+ Styles]',
            'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding',
            f'Style: R2L,{self.font},{self.fontsize},&H{self.opacity}FFFFFF,&H{self.opacity}000000,&H{self.opacity}{self.outlinecolor},&H4F0000FF,-1,0,0,0,100,100,0,0,1,{self.outlinesize},0,1,0,0,0,0',
            f'Style: message_box,Microsoft YaHei,20,&H00FFFFFF,&H00FFFFFF,&H00000000,&H1E6A5149,1,0,0,0,100.00,100.00,0.00,0.00,1,1,0,7,0,0,0,1',
            f'Style: gift_box,Microsoft YaHei,34,&H00FFFFFF,&H00FFFFFF,&H00000000,&H1E6A5149,1,0,0,0,100.00,100.00,0.00,0.00,1,1,0,7,0,0,0,1',
            '',
            '[Events]',
            'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text',
        ]
    
    # ---- 旧版宽度估算(保留备查,已被 PIL 测宽器替代) ----
    # 问题:对比例字体(W 与 i 都按 0.5 算)和命名字体覆盖不到的字符(楔形/生僻/emoji)
    # 误差可达数倍。新版见 self.measurer(DMR/utils/text_width.py)。
    # def _get_length(self, string:str):
    #     length = 0
    #     for s in string:
    #         if len(s.encode('utf-8')) == 1:
    #             length += 0.5*self.fontsize
    #         else:
    #             length += self.fontsize
    #     return int(length)

    def _get_length(self, string:str):
        """PIL 测宽(字体回退链 + 标定 + 缓存),预测 libass 渲染后的实际像素宽。"""
        return self.measurer.measure(string)

    def open(self, filename):
        with self._lock, open(filename,'w',encoding='utf-8') as f:
            self._filename = filename
            self._track_tails = [None for _ in range(self._ntracks)]
            for info in self.meta_info:
                f.write(info+'\n')

    def add(self, danmu, **kwargs):
        if isinstance(danmu, SuperChatDanmaku):
            return self.add_super_chat(danmu)
        elif isinstance(danmu, (GiftDanmaku, MemberDanmaku)):
            return self.add_gift(danmu)
        elif isinstance(danmu, SimpleDanmaku):
            return self.add_simple(danmu, **kwargs)
        return False

    def add_gift(self, gift):
        """写入一条礼物/会员的 GIFT_DATA 注释行，供直播结束后 GiftConverter 解析。
        格式: ; GIFT_DATA|时间|用户名|第二行内容（gift_coverter_content）"""
        with self._lock:
            if not self._filename:
                raise RuntimeError("ASS file is not open.")
            uname = str(gift.uname or '').replace('|', '/')
            content = str(getattr(gift, 'gift_coverter_content', '') or '').replace('|', '/')
            line = f'; GIFT_DATA|{gift.time}|{uname}|{content}\n'
            with open(self._filename, 'a', encoding='utf-8') as f:
                f.write(line)
        return True

    def _pill_dialogue(self, t0, t1, tid, render_w, pad, color, border):
        """生成一条 libass 药丸描边行(\\p1,只描边),与文字同 \\move 同速。
        Effect=pilldraw 供 emoji 渲染器识别:走 emoji 渲染的弹幕会删掉这条、改在精灵里按真实宽重画。"""
        fs = self.fontsize
        h_pill = fs + 2 * pad
        pill_top = (fs + (fs + self.margin_h) * tid + self.dst) - fs - pad   # 文字底 - 字高 - buff
        return make_pill_line(t0, t1, self.width, -render_w, pill_top, render_w, h_pill,
                              color=color, border=border, opacity=self.opacity,
                              layer=0, effect='pilldraw')

    def add_simple(self,
                danmu:SimpleDanmaku,
                calc_collision=True,
                add_pill=False,
                pill_color=None,
                pad=0,
                border=2
        ):
        """
        添加弹幕到ASS文件
        danmu: 待添加弹幕
        calc_collision: 是否计算冲突,冲突的弹幕将会被自动忽略
        add_pill: 强制给这条弹幕套药丸框(任意弹幕都可,用于测试测宽是否准)。VIP 弹幕自动套。
        pill_color/pad/border: 药丸样式——描边色 RRGGBB(None=用弹幕自身颜色 danmu.color)、上下 buff(像素)、描边粗细。
        """
        # VIP 弹幕自动套药丸;任意弹幕也可经 add_pill=True 套(纯文字也行)。
        use_pill = bool(danmu.is_vip) or bool(add_pill)
        # 药丸颜色：传了就用传入色，否则与弹幕文字同色。
        eff_pill_color = pill_color if pill_color else danmu.color

        # 药丸用 libass 画法画(见 _pill_dialogue):纯文字/普通渲染下宽度本就准。
        # 含 emoji/表情、需走 emoji 引擎渲染的弹幕,渲染时会删掉这条 libass 药丸、
        # 在精灵里按真实宽度重画(只改长度),所以最终一定贴合。
        # 文字在药丸内左右各缩进 pad_x(=半圆半径);render_w 用于碰撞预留与滚动范围。
        fs = self.fontsize
        text_w = self._get_length(danmu.text)
        if use_pill:
            pad_x = (fs + 2 * pad) / 2
            render_w = text_w + 2 * pad_x
            pill_name = f'pill#{eff_pill_color}#{int(pad)}#{int(border)}'
        else:
            pad_x = 0
            render_w = text_w
            pill_name = ''

        tid, max_dist = 0, -1e5 # -100000.0

        # 计算给出弹幕到指定弹幕的距离(用各自的"渲染宽度",含药丸留白)
        def tail_dist(tail_dm:SimpleDanmaku, tic:float):
            if not tail_dm:
                return 1e5
            dm_length = getattr(tail_dm, '_render_width', None)
            if dm_length is None:
                dm_length = self._get_length(tail_dm.text)
            dist = (tic - tail_dm.time) * (dm_length + self.width) / self.dmduration - dm_length
            return dist

        for i, tail_dm in enumerate(self._track_tails):
            dist = tail_dist(tail_dm, danmu.time)
            if dist > 0.2 * self.width and dist > self.margin_w:
                tid = i
                max_dist = dist
                break
            if dist > max_dist:
                max_dist = dist
                tid = i

        if calc_collision and max_dist < self.margin_w:
            return False

        t0 = '%02d:%02d:%05.2f'%sec2hms(danmu.time)
        t1 = '%02d:%02d:%05.2f'%sec2hms(danmu.time + self.dmduration)
        text_color = RGB2BGR(danmu.color)   # 文字色(勿覆盖药丸的 color 参数)

        # 文字行(\an1 来自样式)。套药丸时文字左右各缩进 pad_x;Name 带标记供 emoji 渲染器识别。
        y = fs + (fs + self.margin_h) * tid
        x0, x1 = self.width + pad_x, -(text_w + pad_x)
        dm_info = f'Dialogue: 0,{t0},{t1},R2L,{pill_name},0,0,0,,'
        dm_info += '{\\move(%d,%d,%d,%d)}'%(x0, y + self.dst, x1, y + self.dst)
        dm_info += fR'{{\alpha&H{self.opacity}&\1c&H{text_color}&}}'
        dm_info += danmu.text

        with self._lock, open(self._filename, 'a', encoding='utf-8') as f:
            f.write(dm_info + '\n')
            if use_pill:   # 紧跟一行 libass 药丸描边(emoji 渲染器靠"紧随文字行"来配对)
                f.write(self._pill_dialogue(t0, t1, tid, render_w, pad, eff_pill_color, border) + '\n')

        danmu._render_width = render_w   # 供后续弹幕碰撞计算用(含药丸留白)
        self._track_tails[tid] = danmu

        return True

    def add_super_chat(self, super_chat: SuperChatDanmaku):
        """
        写入一条 SuperChat 的 SC_DATA 注释行，供直播结束后 SCConverter 解析。
        格式: ; SC_DATA|时间|用户名|价格|单位|内容|上框色|下框色|名字色|内容色
               |sc时长|上框透明度|下框透明度|名字描边宽度|名字描边色|内容描边宽度|内容描边色
        """
        with self._lock:
            if not self._filename:
                raise RuntimeError("ASS file is not open.")

            # ===== 以下参数写入 SC_DATA，由 SCConverter 逐条读取，可自由修改 =====
            sc_duration          = super_chat.sc_duration
            top_opacity          = 0.7              # 上框不透明度（0=全透明，1=不透明）
            bottom_opacity       = 0.3              # 下框不透明度
            name_border_width    = 0                # 名字描边宽度（0=不描边）
            name_border_color    = darken_for_border(super_chat.background_color)
            content_border_width = 1                # 内容描边宽度（0=不描边）
            content_border_color = darken_for_border(super_chat.background_bottom_color)
            # ===================================================================
            content = super_chat.content or ''
            current_time = super_chat.time
            bg_color        = RGB2BGR(super_chat.background_color.lstrip('#'))
            bg_bottom_color = RGB2BGR(super_chat.background_bottom_color.lstrip('#'))
            name_color      = RGB2BGR(super_chat.name_color.lstrip('#'))
            content_color   = RGB2BGR(super_chat.content_color.lstrip('#'))
            sc_data_line = (
                f'; SC_DATA|{current_time}|{super_chat.uname}|{super_chat.price}|'
                f'{super_chat.price_unit}|{content}|{bg_color}|'
                f'{bg_bottom_color}|{name_color}|'
                f'{content_color}|{sc_duration}|{top_opacity}|{bottom_opacity}|'
                f'{name_border_width}|{name_border_color}|{content_border_width}|{content_border_color}\n'
            )

            with open(self._filename, 'a', encoding='utf-8') as f:
                f.write(sc_data_line)

            self._super_chat_tails.append(super_chat)

    def close(self):
        del self._filename
        del self._track_tails