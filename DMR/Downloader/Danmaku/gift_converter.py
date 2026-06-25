"""
GiftConverter：把 AssWriter 实时写入的 GIFT_DATA 注释行转换为带动画的礼物弹幕。

结构完全参考 SCConverter（复用其排队/推挤/落回算法、两栏盒子、高度计算），区别：
  - 从屏幕「右侧」滑入、向右侧滑出（SC 是左侧）
  - 第一栏（上框）：用户名；第二栏（下框）：送出{礼物名}x{数量}
  - 上下框都是白色 0.1 透明 + 黑色描边；中间分割线由两框各自的描边自然产生
  - 时长固定（不分档），价格仅一个最低过滤
  - 退场固定 slide_dur（不会因时长不足突然消失）

GIFT_DATA 注释行格式（由 AssWriter 写入）：
  ; GIFT_DATA|时间|用户名|礼物名|数量|total_price_cny
"""

from .sc_converter import SCConverter, _fmt_time, _hex_opacity

_GIFT_DATA_PREFIX = '; GIFT_DATA|'

# ── 布局尺寸（固定）──
_FONTSIZE         = 30
_BOX_WIDTH        = 360
_BOX_TOP_HEIGHT   = 40    # 上框（名字栏）高度
_PADDING_V        = 9     # 下框内容上边距
_PADDING_V_BOTTOM = 14    # 下框内容下边距
_CORNER_RADIUS    = 40    # 圆角半径（越大越像药丸）
_MARGIN_RIGHT     = 10    # 矩形距屏幕右边距
_TEXT_PADDING     = 6     # 文字内边距
_ANCHOR_Y_RATIO   = 0.88  # 最新一条底边位置（屏幕高度比例），默认 0.88（可由 gift_dm_args 传入覆盖）

# ── 外观默认值（颜色=RGB #RRGGBB；不透明度=0~1，1=完全不透明；描边=宽度/颜色）──
# 这些都是 __init__ 参数的默认值，可在构造时覆盖。
_TOP_BG       = '#F0A8D4'; _TOP_OPACITY  = 0.7   # 上框（名字栏）背景色 / 不透明度
_BOT_BG       = '#F8C8E8'; _BOT_OPACITY  = 0.7   # 下框（内容栏）背景色 / 不透明度
_NAME_COLOR   = '#000000'; _NAME_OPACITY = 0.7   # 名字 颜色 / 不透明度
_CONT_COLOR   = '#000000'; _CONT_OPACITY = 0.7   # 内容 颜色 / 不透明度
_NAME_OUTLINE = 0;         _CONT_OUTLINE = 0     # 名字 / 内容 文字描边宽度
_TEXT_BORDC   = '#FFFFFF'                        # 文字描边色（名字与内容共用）


def _rgb2bgr(h):
    h = str(h).lstrip('#').zfill(6)
    return (h[4:6] + h[2:4] + h[0:2]).upper()


class GiftConverter(SCConverter):
    def __init__(self,
                 screen_width=1080,
                 screen_height=1920,
                 buff=10,
                 push_duration_ms=300,
                 slide_dur=0.5,
                 wrap_width=15,   # 换行宽度（中文算1，英文/ASCII算0.5）
                 # 时长固定（价格过滤由 danmaku.py 的 gift_dm_available 上游统一处理）
                 duration=10,
                 anchor_y_ratio=_ANCHOR_Y_RATIO,   # 由 gift_dm_args 传入，默认 0.88
                 # ── 外观（颜色 RGB / 不透明度 0~1 / 描边宽度、颜色）──
                 top_bg=_TOP_BG,   top_opacity=_TOP_OPACITY,     # 上框 颜色 / 不透明度
                 bot_bg=_BOT_BG,   bot_opacity=_BOT_OPACITY,     # 下框 颜色 / 不透明度
                 name_color=_NAME_COLOR, name_opacity=_NAME_OPACITY, name_outline=_NAME_OUTLINE,   # 名字 颜色/不透明度/描边宽
                 cont_color=_CONT_COLOR, cont_opacity=_CONT_OPACITY, cont_outline=_CONT_OUTLINE,   # 内容 颜色/不透明度/描边宽
                 text_border_color=_TEXT_BORDC,    # 文字描边色（名字与内容共用）
                 ):
        super().__init__(
            screen_width     = screen_width,
            screen_height    = screen_height,
            fontsize         = _FONTSIZE,
            box_width        = _BOX_WIDTH,
            box_top_height   = _BOX_TOP_HEIGHT,
            padding_v        = _PADDING_V,
            padding_v_bottom = _PADDING_V_BOTTOM,
            corner_radius    = _CORNER_RADIUS,
            text_padding     = _TEXT_PADDING,
            buff             = buff,
            anchor_y_ratio   = anchor_y_ratio,
            push_duration_ms = push_duration_ms,
            slide_dur        = slide_dur,
            wrap_width       = wrap_width
        )

        self.margin_right = _MARGIN_RIGHT
        # 颜色存为 BGR；不透明度存为 ASS 的 alpha 两位 hex（_hex_opacity：1→00 不透明，0→FF 全透明）
        self.top_bg  = _rgb2bgr(top_bg);     self.top_alpha  = _hex_opacity(top_opacity)
        self.bot_bg  = _rgb2bgr(bot_bg);     self.bot_alpha  = _hex_opacity(bot_opacity)
        self.name_c  = _rgb2bgr(name_color); self.name_alpha = _hex_opacity(name_opacity); self.name_bord = name_outline
        self.cont_c  = _rgb2bgr(cont_color); self.cont_alpha = _hex_opacity(cont_opacity); self.cont_bord = cont_outline
        self.text_bordc = _rgb2bgr(text_border_color)
        self.duration   = float(duration)

        # 右侧坐标
        self.box_x1 = screen_width - self.margin_right - _BOX_WIDTH   # 静止时矩形左边缘
        self.box_x0 = screen_width + self.margin_right                # 屏幕外（右）左边缘
        self.slide_ms = int(slide_dur * 1000)

    # ── 解析 GIFT_DATA（固定时长；价格过滤已在上游 gift_dm_available 完成）──
    # 格式: ; GIFT_DATA|时间|用户名|第二行内容
    def _parse_gift_data(self, lines):
        gifts = []
        for line in lines:
            s = line.rstrip()
            if not s.startswith(_GIFT_DATA_PREFIX):
                continue
            parts = s[len(_GIFT_DATA_PREFIX):].split('|')
            if len(parts) < 3:
                continue
            try:
                gifts.append({
                    'time'        : float(parts[0]),
                    'uname'       : parts[1],
                    'content'     : parts[2],
                    'sc_duration' : self.duration,
                })
            except (ValueError, IndexError):
                continue
        return gifts

    # ── 退场固定 slide_dur（沿用父类 push/fall，末尾补完整退场）──
    # 退场固定在「退场起点(cut)时所在的行」滑出，忽略退场期间被新弹幕 push 上移的情况
    def _finalize_segments(self, entries):
        sd = self.slide_dur
        # 先从原始段算出每条在 cut 时刻的 y（此时父类还没把 segments 改成 typed）
        cut_y = {}
        for e in entries:
            raw = e['segments']
            t1  = raw[-1][1]
            cut = max(raw[0][0], t1 - sd)
            y = raw[-1][3]
            for s0, s1, ys, ye in raw:
                if s0 <= cut < s1:
                    y = ye; break
            cut_y[id(e)] = y

        super()._finalize_segments(entries)
        for e in entries:
            typed = e['segments']
            t1  = typed[-1][1]
            cut = max(typed[0][0], t1 - sd)
            fy  = cut_y[id(e)]
            out = []
            for s0, s1, ys, ye, st in typed:
                if s1 <= cut + 1e-6:
                    out.append((s0, s1, ys, ye, st))
                elif s0 < cut:
                    # 跨越 cut 的段裁到 cut，并把终点 y 拉回 cut 时的行，避免退场期 push 抬升
                    out.append((s0, cut, ys, fy, 'static' if st in ('exit', 'static') else st))
            out.append((cut, t1, fy, fy, 'exit'))
            e['segments'] = out

    # ── 转换主流程 ──
    def convert(self, input_path: str, output_path: str):
        with open(input_path, encoding='utf-8') as f:
            lines = f.readlines()
        gift_list = self._parse_gift_data(lines)
        entries = self._compute_positions(gift_list)
        self._finalize_segments(entries)
        with open(output_path, 'w', encoding='utf-8') as f:
            for line in lines:
                if line.rstrip().startswith(_GIFT_DATA_PREFIX):
                    continue
                f.write(line)
        with open(output_path, 'a', encoding='utf-8') as f:
            for entry in entries:
                f.write(self._build_dialogue(entry))
        return len(entries)

    # ── 渲染单条礼物（两栏盒子，右滑入）──
    def _build_dialogue(self, entry):
        sc = entry['sc']
        total_h, bh, n_lines = self._box_height(sc['content'])
        th = self.box_top_height
        w  = self.box_width
        r  = self.corner_radius
        fs = self.fontsize
        pv = self.padding_v
        tp = self.text_padding
        d  = self.push_dur_ms
        sm = self.slide_ms
        x1 = self.box_x1
        x0 = self.box_x0

        name = sc['uname']
        content = sc['content']
        fmt_content = self._wrap_content(content)

        top_style  = f'\\c&H{self.top_bg}\\1a&H{self.top_alpha}&\\bord0\\shad0\\p1'
        bot_style  = f'\\c&H{self.bot_bg}\\1a&H{self.bot_alpha}&\\bord0\\shad0\\p1'
        name_style = f'\\c&H{self.name_c}\\1a&H{self.name_alpha}&\\fs{fs}\\b1\\bord{self.name_bord}\\3c&H{self.text_bordc}&\\q2'
        cont_style = f'\\c&H{self.cont_c}\\1a&H{self.cont_alpha}&\\fs{fs}\\b1\\bord{self.cont_bord}\\3c&H{self.text_bordc}&\\q2'

        # 上框（圆角顶、方底）/ 下框（方顶、圆角底）—— 两框描边在 y=th 处自然形成分割线
        top_path = f'm {r} 0 l {w-r} 0 b {w} 0 {w} 0 {w} {r} l {w} {th} l 0 {th} l 0 {r} b 0 0 0 0 {r} 0'
        bot_path = (f'm 0 0 l {w} 0 l {w} {bh-r} b {w} {bh} {w} {bh} {w-r} {bh} '
                    f'l {r} {bh} b 0 {bh} 0 {bh} 0 {bh-r} l 0 0')

        fad_map = {'entry': f'\\fad({sm},0)', 'push': '\\fad(0,0)',
                   'static': '\\fad(0,0)', 'exit': f'\\fad(0,{sm})'}

        out = []
        for t0, t1, ys, ye, stype in entry['segments']:
            s0 = _fmt_time(t0); s1 = _fmt_time(t1); fad = fad_map[stype]
            if stype == 'entry':
                y = ye
                top_mv  = f'\\move({x0},{y},{x1},{y},0,{sm})'
                bot_mv  = f'\\move({x0},{y+th},{x1},{y+th},0,{sm})'
                name_mv = f'\\an7\\move({x0+tp},{y+5},{x1+tp},{y+5},0,{sm})'
                cont_mv = f'\\an7\\move({x0+tp},{y+th+pv},{x1+tp},{y+th+pv},0,{sm})'
            elif stype == 'push':
                top_mv  = f'\\move({x1},{ys},{x1},{ye},0,{d})'
                bot_mv  = f'\\move({x1},{ys+th},{x1},{ye+th},0,{d})'
                name_mv = f'\\an7\\move({x1+tp},{ys+5},{x1+tp},{ye+5},0,{d})'
                cont_mv = f'\\an7\\move({x1+tp},{ys+th+pv},{x1+tp},{ye+th+pv},0,{d})'
            elif stype == 'static':
                y = ye
                top_mv  = f'\\pos({x1},{y})'
                bot_mv  = f'\\pos({x1},{y+th})'
                name_mv = f'\\an7\\pos({x1+tp},{y+5})'
                cont_mv = f'\\an7\\pos({x1+tp},{y+th+pv})'
            else:  # exit 向右滑出
                y = ye
                top_mv  = f'\\move({x1},{y},{x0},{y},0,{sm})'
                bot_mv  = f'\\move({x1},{y+th},{x0},{y+th},0,{sm})'
                name_mv = f'\\an7\\move({x1+tp},{y+5},{x0+tp},{y+5},0,{sm})'
                cont_mv = f'\\an7\\move({x1+tp},{y+th+pv},{x0+tp},{y+th+pv},0,{sm})'

            out.append(
                f'Dialogue: 0,{s0},{s1},gift_box,,0,0,0,,{{{fad}{top_mv}{top_style}}}{top_path}\n'
                f'Dialogue: 0,{s0},{s1},gift_box,,0,0,0,,{{{fad}{bot_mv}{bot_style}}}{bot_path}\n'
                f'Dialogue: 1,{s0},{s1},gift_box,,0,0,0,,{{{fad}{name_mv}{name_style}}}{name}\n'
                f'Dialogue: 1,{s0},{s1},gift_box,,0,0,0,,{{{fad}{cont_mv}{cont_style}}}{fmt_content}\n'
            )
        return ''.join(out)
