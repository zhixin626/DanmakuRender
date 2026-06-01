"""
SCConverter：将 AssWriter 实时写入的 ASS 文件转换为带动态动画的最终版本。

工作流程：
  AssWriter.add_super_chat()
      ↓ 实时写入（直播中）
  raw.ass（含 SC_DATA 注释行 + 预览 Dialogue 行）
      ↓ 直播结束后调用 SCConverter.convert()
  output.ass（动态 push/slide-in/slide-out 动画）

SC_DATA 注释行格式（由 AssWriter 写入）：
  ; SC_DATA|时间|用户名|价格|单位|内容|上框色|下框色|名字色|内容色
"""

import os

# SC_DATA 注释行前缀
_SC_DATA_PREFIX = '; SC_DATA|'
# 末尾固定的4个颜色字段（6位十六进制）数量
_SC_DATA_COLOR_FIELDS = 4
# message_box 预览行前缀（用于从输出中剔除预览行）
_PREVIEW_PREFIX = 'Dialogue: '


def _hex_opacity(opacity):
    return hex(255 - int(opacity * 255))[2:].zfill(2)

def _sec2hms(sec):
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return h, m, s

def _fmt_time(sec):
    h, m, s = _sec2hms(sec)
    return '%01d:%02d:%05.2f' % (h, m, s)


class SCConverter:
    """
    SuperChat 动态动画转换器。

    参数均可在构造时覆盖，默认值与 AssWriter 中的预览参数一致。
    """

    def __init__(self,
                 screen_width=1080,
                 screen_height=1920,
                 fontsize=30,
                 box_width=360,         # 矩形宽度
                 box_top_height=40,     # 上框高度
                 padding_v=10,          # 下框上下内边距
                 corner_radius=40,      # 圆角半径
                 margin_left=10,        # 矩形距屏幕左边距
                 text_padding=6,        # 文字内边距
                 buff=10,               # 相邻SC间距
                 anchor_y_ratio=0.8,    # 新SC出现位置（屏幕高度比例）
                 push_duration_ms=300,  # push动画时长（毫秒）
                 slide_dur=0.5,         # 滑入/滑出时长（秒）
                 # sc_duration/opacity/border 均从 SC_DATA 逐条读取，不在此配置
                 ):
        self.screen_width    = screen_width
        self.screen_height   = screen_height
        self.fontsize        = fontsize
        self.box_width       = box_width
        self.box_top_height  = box_top_height
        self.line_height     = fontsize + 8
        self.padding_v       = padding_v
        self.corner_radius   = corner_radius
        self.margin_left     = margin_left
        self.text_padding    = text_padding
        self.buff            = buff
        self.anchor_y        = int(screen_height * anchor_y_ratio)
        self.push_dur_ms     = push_duration_ms
        self.push_dur_s      = push_duration_ms / 1000.0
        self.slide_dur       = slide_dur

        # 派生坐标
        self.slide_x  = -(box_width + margin_left)      # 矩形左边缘在屏幕外的 x
        self.text_x0  = self.slide_x + text_padding     # 文字滑入起始 x（左锚）
        self.text_x1  = margin_left + text_padding      # 文字静止 x（左锚）
        self.price_x0 = self.slide_x + box_width - text_padding   # 价格滑入起始 x（右锚）
        self.price_x1 = margin_left + box_width - text_padding    # 价格静止 x（右锚）

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def convert(self, input_path: str, output_path: str):
        """
        读取 input_path（含 SC_DATA 注释行的 ASS），
        输出带动态动画的 output_path。
        """
        with open(input_path, encoding='utf-8') as f:
            lines = f.readlines()

        sc_list    = self._parse_sc_data(lines)
        entries    = self._compute_positions(sc_list)
        self._finalize_segments(entries)

        # 写非SC行（去掉预览行和SC_DATA注释行，保留普通弹幕及文件头）
        with open(output_path, 'w', encoding='utf-8') as f:
            for line in lines:
                s = line.rstrip()
                if s.startswith(_SC_DATA_PREFIX):
                    continue    # 去掉 SC_DATA 注释行
                if self._is_preview_line(s):
                    continue    # 去掉预览 Dialogue 行
                f.write(line)

        # 追加动态 SC
        with open(output_path, 'a', encoding='utf-8') as f:
            for entry in entries:
                f.write(self._build_dialogue(entry))

        sc_count = len(entries)
        return sc_count

    # ------------------------------------------------------------------
    # 内部：解析
    # ------------------------------------------------------------------

    def _is_preview_line(self, line: str) -> bool:
        """判断是否是 add_super_chat 写入的预览 Dialogue 行。"""
        if not line.startswith(_PREVIEW_PREFIX):
            return False
        # Dialogue 格式: "Dialogue: layer,t0,t1,style,..."
        # 取第4个逗号分隔字段（style）是否为 message_box
        parts = line.split(',', 5)
        return len(parts) >= 5 and parts[3] == 'message_box'

    def _parse_sc_data(self, lines):
        """
        从文件行中提取所有 SC_DATA 注释行，返回 SC 列表。

        SC_DATA 格式（由 AssWriter 写入）：
          ; SC_DATA|时间|用户名|价格|单位|内容|上框色|下框色|名字色|内容色
          |sc时长|上框透明度|下框透明度|名字描边|名字描边色|内容描边|内容描边色

        内容字段可能含 |，因此末尾固定字段从右侧数，中间剩余部分为内容。
        末尾固定11字段：bg bg_bottom name_color content_color
                        sc_duration top_opacity bottom_opacity
                        name_border_width name_border_color content_border_width content_border_color
        """
        # 默认值（兼容旧格式，旧格式无后7个字段）
        DEFAULTS = {
            'sc_duration'         : 60,
            'top_opacity'         : 0.7,
            'bottom_opacity'      : 0.1,
            'name_border_width'   : 0,
            'name_border_color'   : 'FFFFFF',
            'content_border_width': 1,
            'content_border_color': '000000',
        }

        sc_list = []
        for line in lines:
            s = line.rstrip()
            if not s.startswith(_SC_DATA_PREFIX):
                continue
            parts = s[len(_SC_DATA_PREFIX):].split('|')
            # 最少9个字段（旧格式），最多有后11个固定字段（新格式）
            if len(parts) < 9:
                continue
            try:
                # 判断是否是新格式（末尾有额外7个字段）
                is_new = len(parts) >= 15
                suffix_count = 11 if is_new else 4   # 末尾固定字段数

                sc = {
                    'time'            : float(parts[0]),
                    'uname'           : parts[1],
                    'price'           : parts[2],
                    'price_unit'      : parts[3],
                    'content'         : '|'.join(parts[4:-suffix_count]),
                    'bg_color'        : parts[-suffix_count],
                    'bg_bottom_color' : parts[-suffix_count + 1],
                    'name_color'      : parts[-suffix_count + 2],
                    'content_color'   : parts[-suffix_count + 3],
                }

                if is_new:
                    sc.update({
                        'sc_duration'         : float(parts[-7]),
                        'top_opacity'         : float(parts[-6]),
                        'bottom_opacity'      : float(parts[-5]),
                        'name_border_width'   : int(parts[-4]),
                        'name_border_color'   : parts[-3],
                        'content_border_width': int(parts[-2]),
                        'content_border_color': parts[-1],
                    })
                else:
                    sc.update(DEFAULTS)

                sc_list.append(sc)
            except (ValueError, IndexError):
                continue
        return sc_list

    # ------------------------------------------------------------------
    # 内部：位置计算
    # ------------------------------------------------------------------

    def _box_height(self, content):
        """计算一条SC的总高度（上框 + 下框，不含 buff）。"""
        n_lines = max(1, len(content) // 15 + (1 if len(content) % 15 else 0)) if content else 1
        bh = n_lines * self.line_height + self.padding_v * 2
        return self.box_top_height + bh, bh, n_lines

    def _compute_positions(self, sc_list):
        """
        为每条SC计算 segments 列表（未带类型）。
        segment: (t0, t1, y_start, y_end)

        规则：
          - 同时出现的SC强制排队，间隔1秒
          - 新SC出现在 anchor_y，旧SC从下往上重新计算理想位置
        """
        active = []
        result = []
        last_t0 = -9999.0

        for sc in sc_list:
            raw_t0 = sc['time']

            # 同时出现强制排队
            effective_t0 = max(raw_t0, last_t0 + 1.0) if raw_t0 <= last_t0 else raw_t0
            last_t0 = effective_t0
            t1 = effective_t0 + sc['sc_duration']

            total_h, bh, n_lines = self._box_height(sc['content'])

            # 清除已过期的SC
            active = [a for a in active if a['t1'] > effective_t0]

            # 从下往上重新计算每个活跃SC的理想位置
            avail = self.anchor_y - self.buff
            for a in sorted(active, key=lambda x: -x['current_y']):
                ah      = a['total_h']   # 已缓存，避免重复计算
                ideal_y = avail - ah
                avail    = ideal_y - self.buff
                if ideal_y != a['current_y']:
                    # 截断上一段（旧元素在 push 时刻消失）
                    last = a['segments'][-1]
                    a['segments'][-1] = (last[0], effective_t0, last[2], last[3])
                    # 新段：从当前位置移到理想位置
                    a['segments'].append((effective_t0, a['t1'], a['current_y'], ideal_y))
                    a['current_y'] = ideal_y

            entry = {
                'sc'       : sc,
                'total_h'  : total_h,   # 缓存高度，push时直接用
                'bh'       : bh,
                'n_lines'  : n_lines,
                't0'       : effective_t0,
                't1'       : t1,
                'current_y': self.anchor_y,
                'segments' : [(effective_t0, t1, self.anchor_y, self.anchor_y)],
            }
            active.append(entry)
            result.append(entry)

        return result

    # ------------------------------------------------------------------
    # 内部：segment 类型化（拆出 exit 段）
    # ------------------------------------------------------------------
    # segment 类型：
    #   'entry'  首次出现，从左侧滑入
    #   'push'   被新SC顶上去，向上移动
    #   'static' 静止停留
    #   'exit'   自然消失，向左滑出

    def _finalize_segments(self, entries):
        """给每条SC的 segments 打类型标记，并在末尾拆出 exit 段。"""
        sd = self.slide_dur
        pd = self.push_dur_s

        for entry in entries:
            raw   = entry['segments']
            typed = []

            for i, (t0, t1, ys, ye) in enumerate(raw):
                is_first = (i == 0)
                is_last  = (i == len(raw) - 1)
                dur = t1 - t0

                if is_first and is_last:
                    # 单段SC（从未被push），拆成 entry + exit
                    if dur > sd * 2:
                        typed.append((t0, t1 - sd, ys, ye, 'entry'))
                        typed.append((t1 - sd, t1, ye, ye, 'exit'))
                    else:
                        typed.append((t0, t1, ys, ye, 'entry'))

                elif is_first:
                    typed.append((t0, t1, ys, ye, 'entry'))

                elif is_last:
                    if ys != ye:
                        # push段同时是最后一段：先push，再exit
                        push_end = t0 + pd
                        if push_end >= t1 - sd:
                            typed.append((t0, t1, ys, ye, 'push'))
                        else:
                            typed.append((t0, push_end, ys, ye, 'push'))
                            if t1 - push_end > sd:
                                typed.append((push_end, t1 - sd, ye, ye, 'static'))
                            typed.append((t1 - sd, t1, ye, ye, 'exit'))
                    else:
                        # 静止段是最后一段
                        if dur > sd:
                            typed.append((t0, t1 - sd, ys, ye, 'static'))
                            typed.append((t1 - sd, t1, ye, ye, 'exit'))
                        else:
                            typed.append((t0, t1, ys, ye, 'exit'))

                else:
                    typed.append((t0, t1, ys, ye, 'push' if ys != ye else 'static'))

            entry['segments'] = typed

    # ------------------------------------------------------------------
    # 内部：渲染成 ASS Dialogue 行
    # ------------------------------------------------------------------

    def _build_dialogue(self, entry):
        """将一条SC的所有 segments 渲染为 ASS Dialogue 字符串。"""
        sc   = entry['sc']
        bh   = entry['bh']
        r    = self.corner_radius
        w    = self.box_width
        th   = self.box_top_height
        fs   = self.fontsize
        pv   = self.padding_v
        d    = self.push_dur_ms

        toph = _hex_opacity(sc['top_opacity'])
        both = _hex_opacity(sc['bottom_opacity'])
        nb   = f'\\bord{sc["name_border_width"]}\\3c&H{sc["name_border_color"]}&'
        cb   = f'\\bord{sc["content_border_width"]}\\3c&H{sc["content_border_color"]}&'

        # 格式化内容（每15字换行）
        content = sc['content']
        fmt_content = '\\N'.join(content[i:i+15] for i in range(0, len(content), 15)) if content else ''

        fad_map = {'entry': '\\fad(500,0)', 'push': '\\fad(0,0)',
                   'static': '\\fad(0,0)',  'exit': '\\fad(0,500)'}

        lines = []
        for seg_t0, seg_t1, ys, ye, stype in entry['segments']:
            s0 = _fmt_time(seg_t0)
            s1 = _fmt_time(seg_t1)
            fad = fad_map[stype]

            if stype == 'entry':
                y = ye
                top_mv   = f'\\move({self.slide_x},{y},{self.margin_left},{y},0,500)'
                topb_mv  = f'\\move({self.slide_x},{y+th},{self.margin_left},{y+th},0,500)'
                name_mv  = f'\\an7\\move({self.text_x0},{y+5},{self.text_x1},{y+5},0,500)'
                price_mv = f'\\an9\\move({self.price_x0},{y+5},{self.price_x1},{y+5},0,500)'
                cont_mv  = f'\\move({self.text_x0},{y+th+pv},{self.text_x1},{y+th+pv},0,500)'

            elif stype == 'push':
                top_mv   = f'\\move({self.margin_left},{ys},{self.margin_left},{ye},0,{d})'
                topb_mv  = f'\\move({self.margin_left},{ys+th},{self.margin_left},{ye+th},0,{d})'
                name_mv  = f'\\an7\\move({self.text_x1},{ys+5},{self.text_x1},{ye+5},0,{d})'
                price_mv = f'\\an9\\move({self.price_x1},{ys+5},{self.price_x1},{ye+5},0,{d})'
                cont_mv  = f'\\move({self.text_x1},{ys+th+pv},{self.text_x1},{ye+th+pv},0,{d})'

            elif stype == 'static':
                y = ye
                top_mv   = f'\\pos({self.margin_left},{y})'
                topb_mv  = f'\\pos({self.margin_left},{y+th})'
                name_mv  = f'\\an7\\pos({self.text_x1},{y+5})'
                price_mv = f'\\an9\\pos({self.price_x1},{y+5})'
                cont_mv  = f'\\pos({self.text_x1},{y+th+pv})'

            else:  # exit
                y = ye
                top_mv   = f'\\move({self.margin_left},{y},{self.slide_x},{y},0,500)'
                topb_mv  = f'\\move({self.margin_left},{y+th},{self.slide_x},{y+th},0,500)'
                name_mv  = f'\\an7\\move({self.text_x1},{y+5},{self.text_x0},{y+5},0,500)'
                price_mv = f'\\an9\\move({self.price_x1},{y+5},{self.price_x0},{y+5},0,500)'
                cont_mv  = f'\\move({self.text_x1},{y+th+pv},{self.text_x0},{y+th+pv},0,500)'

            lines.append(
                # 上框
                f'Dialogue: 0,{s0},{s1},message_box,,0,0,0,,'
                f'{{{fad}{top_mv}\\c&H{sc["bg_color"]}\\1a&H{toph}&\\shad0\\p1}}'
                f'm {r} 0 l {w-r} 0 b {w} 0 {w} 0 {w} {r} l {w} {th} l 0 {th} l 0 {r} b 0 0 0 0 {r} 0\n'
                # 下框
                f'Dialogue: 0,{s0},{s1},message_box,,0,0,0,,'
                f'{{{fad}{topb_mv}\\c&H{sc["bg_bottom_color"]}\\1a&H{both}&\\shad0\\p1}}'
                f'm 0 0 l {w} 0 l {w} {bh-r} b {w} {bh} {w} {bh} {w-r} {bh} l {r} {bh} b 0 {bh} 0 {bh} 0 {bh-r} l 0 0\n'
                # 用户名（左对齐）
                f'Dialogue: 1,{s0},{s1},message_box,,0,0,0,,'
                f'{{{fad}{name_mv}\\c&H{sc["name_color"]}\\fs{fs}\\b1{nb}\\q2}}{sc["uname"]}\n'
                # 价格（右对齐）
                f'Dialogue: 1,{s0},{s1},message_box,,0,0,0,,'
                f'{{{fad}{price_mv}\\c&H{sc["name_color"]}\\fs{fs}\\b1{nb}\\q2}}{sc["price"]}{sc["price_unit"]}\n'
                # 内容
                f'Dialogue: 1,{s0},{s1},message_box,,0,0,0,,'
                f'{{{fad}{cont_mv}\\c&H{sc["content_color"]}\\fs{fs}\\b1{cb}\\q2}}{fmt_content}\n'
            )

        return ''.join(lines)
