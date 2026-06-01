from datetime import datetime
import threading
from DMR.utils import *
from itertools import cycle


__all__ = ['AssWriter']

def hex_opacity(opacity):
    return hex(255-int(opacity*255))[2:].zfill(2)
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
            '',
            '[Events]',
            'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text',
        ]
    
    def _get_length(self, string:str):
        length = 0
        for s in string:
            if len(s.encode('utf-8')) == 1:
                length += 0.5*self.fontsize
            else:
                length += self.fontsize

        return int(length)

    def open(self, filename):
        with self._lock, open(filename,'w',encoding='utf-8') as f:
            self._filename = filename
            self._track_tails = [None for _ in range(self._ntracks)]
            for info in self.meta_info:
                f.write(info+'\n')

    def add(self, danmu, **kwargs):
        if isinstance(danmu, SuperChatDanmaku):
            return self.add_super_chat(danmu)
        elif isinstance(danmu, SimpleDanmaku):
            return self.add_simple(danmu, **kwargs)
        return False

    def add_simple(self, danmu:SimpleDanmaku, calc_collision=True):
        """
        添加弹幕到ASS文件
        danmu: 待添加弹幕
        calc_collision: 是否计算冲突,冲突的弹幕将会被自动忽略
        """
        tid, max_dist = 0, -1e5 # -100000.0

        # 计算给出弹幕到指定弹幕的距离
        def tail_dist(tail_dm:SimpleDanmaku, tic:float):
            if not tail_dm:
                return 1e5
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

        # danmu.text 被上一层danmaku.py决定，vip弹幕已经被上一层改为 f"{dm.uname}:{dm.content}"
        dm_length = self._get_length(danmu.text)
        x0 = self.width
        x1 = -dm_length
        y  = self.fontsize + (self.fontsize + self.margin_h) * tid

        t0 = '%02d:%02d:%05.2f'%sec2hms(danmu.time)
        t1 = '%02d:%02d:%05.2f'%sec2hms(danmu.time + self.dmduration)


        dm_info = f'Dialogue: 0,{t0},{t1},R2L,,0,0,0,,'
        dm_info += '{\\move(%d,%d,%d,%d)}'%(x0, y + self.dst, x1, y + self.dst)


        if danmu.dtype == "gift":
            # 礼物弹幕
            outline_color   = RGB2BGR("ffffff") # white
            text_color      = RGB2BGR("000000") # black
            gift_dm_opacity = hex_opacity(self.gift_dm_args.get("gift_dm_opacity",1))
            outline_width   = 10
            dm_info += fR"{{\alpha&H{gift_dm_opacity}&\1c&H{text_color}&\bord{outline_width}\3c&H{outline_color}&}}"
            dm_info += danmu.text
        else:
            # VIP弹幕
            if danmu.is_vip:
                dm_info += fR'{{\alpha&H{self.opacity}&\1c&H{RGB2BGR(danmu.color)}&\i1}}' # 斜体
                dm_info += f"{danmu.uname}:"
                dm_info += fR"{{\i0}}" # 还原斜体
                dm_info += danmu.content

            else:
                # 普通弹幕，正常上色
                dm_info += fR'{{\alpha&H{self.opacity}&\1c&H{RGB2BGR(danmu.color)}&}}'
                dm_info += danmu.text

        with self._lock, open(self._filename, 'a', encoding='utf-8') as f:
            f.write(dm_info + '\n')
        
        self._track_tails[tid] = danmu

        return True

    def add_super_chat(self, super_chat: SuperChatDanmaku):
        """
        写入一条 SuperChat。
        同时写两部分：
          1. SC_DATA 注释行 —— 存储原始结构化数据，供后处理的 SCConverter 解析
          2. 预览用 ASS 行  —— 简单静态堆叠，供直播结束前实时预览用
        """
        with self._lock:
            if not self._filename:
                raise RuntimeError("ASS file is not open.")

            # ========== 预览参数（与 SCConverter 保持一致）==========
            fontsize       = 30
            sc_duration    = 60
            box_width      = 360
            box_top_height = 40
            line_height    = fontsize + 8
            padding_v      = 10
            corner_radius  = 40
            margin_left    = 10
            text_padding   = 6
            buff           = 10
            base_y         = 100
            top_opacity    = 0.7
            bottom_opacity = 0.1
            # ==========================================================

            content = super_chat.content or ''
            content_lines = [content[i:i+15] for i in range(0, len(content), 15)] or ['']
            formatted_content = '\\N'.join(content_lines)
            n_lines = len(content_lines)
            bh = n_lines * line_height + padding_v * 2  # 下框高度
            th = box_top_height

            # 预览用简单堆叠 y 坐标
            current_time = super_chat.time
            if current_time > self._latest_end_time:
                self._super_chat_state = 0
                self._latest_y = base_y
            self._super_chat_state += 1
            self._latest_end_time = current_time + sc_duration
            y = self._latest_y
            self._latest_y += th + bh + buff
            if y + th + bh > self.height:
                self._latest_y = base_y
                y = base_y
                self._latest_y += th + bh + buff

            t0 = '%02d:%02d:%05.2f' % sec2hms(current_time)
            t1 = '%02d:%02d:%05.2f' % sec2hms(current_time + sc_duration)
            sx   = -(box_width + margin_left)   # 滑入起始 x
            tx0  = sx + text_padding
            tx1  = margin_left + text_padding
            toph = hex_opacity(top_opacity)
            both = hex_opacity(bottom_opacity)
            r    = corner_radius
            w    = box_width

            # ---- 1. SC_DATA 注释行（结构化，供 SCConverter 解析）----
            # 格式: ; SC_DATA|时间|用户名|价格|单位|内容|上框色|下框色|名字色|内容色
            #        |sc时长|上框透明度|下框透明度|名字描边|名字描边色|内容描边|内容描边色
            name_border_width   = 0         # 名字描边宽度（0=不描边）
            name_border_color   = 'FFFFFF'  # 名字描边色（BGR十六进制）
            content_border_width= 1         # 内容描边宽度（0=不描边）
            content_border_color= '000000'  # 内容描边色（BGR十六进制）
            sc_data_line = (
                f'; SC_DATA|{current_time}|{super_chat.uname}|{super_chat.price}|'
                f'{super_chat.price_unit}|{content}|{super_chat.background_color}|'
                f'{super_chat.background_bottom_color}|{super_chat.name_color}|'
                f'{super_chat.content_color}|{sc_duration}|{top_opacity}|{bottom_opacity}|'
                f'{name_border_width}|{name_border_color}|{content_border_width}|{content_border_color}\n'
            )

            # ---- 2. 预览用 ASS（静态堆叠，仅供实时预览）----
            preview = (
                f'Dialogue: 0,{t0},{t1},message_box,,0,0,0,,'
                f'{{\\fad(500,500)\\move({sx},{y},{margin_left},{y},0,500)\\c&H{super_chat.background_color}\\1a&H{toph}&\\shad0\\p1}}'
                f'm {r} 0 l {w-r} 0 b {w} 0 {w} 0 {w} {r} l {w} {th} l 0 {th} l 0 {r} b 0 0 0 0 {r} 0\n'
                f'Dialogue: 0,{t0},{t1},message_box,,0,0,0,,'
                f'{{\\fad(500,500)\\move({sx},{y+th},{margin_left},{y+th},0,500)\\c&H{super_chat.background_bottom_color}\\1a&H{both}&\\shad0\\p1}}'
                f'm 0 0 l {w} 0 l {w} {bh-r} b {w} {bh} {w} {bh} {w-r} {bh} l {r} {bh} b 0 {bh} 0 {bh} 0 {bh-r} l 0 0\n'
                f'Dialogue: 1,{t0},{t1},message_box,,0,0,0,,'
                f'{{\\fad(500,500)\\move({tx0},{y+5},{tx1},{y+5},0,500)\\c&H{super_chat.name_color}\\fs{fontsize}\\b1\\bord0\\q2}}{super_chat.uname} ({super_chat.price}{super_chat.price_unit})\n'
                f'Dialogue: 1,{t0},{t1},message_box,,0,0,0,,'
                f'{{\\fad(500,500)\\move({tx0},{y+th+padding_v},{tx1},{y+th+padding_v},0,500)\\c&H{super_chat.content_color}\\fs{fontsize}\\b1\\bord1\\3c&H000000&\\q2}}{formatted_content}\n'
            )

            with open(self._filename, 'a', encoding='utf-8') as f:
                f.write(sc_data_line)
                f.write(preview)

            self._super_chat_tails.append(super_chat)

    def close(self):
        del self._filename
        del self._track_tails