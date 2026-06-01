"""
从 test.ass（已由 convert_sc.py 转换的4行新格式）生成 1.ass。
新SC固定出现在屏幕3/4处，旧SC被顶上去；同时出现的SC强制排队，间隔1秒。
用法: python convert_sc_dynamic.py [input.ass [output.ass]]
"""

import re
import sys
import os

DEFAULT_INPUT  = r"D:\DanmakuRender\Tasks文件\峰哥\test\test.ass"
DEFAULT_OUTPUT = r"D:\DanmakuRender\Tasks文件\峰哥\test\1.ass"

SC_LINE_RE = re.compile(
    r'^Dialogue: \d+,(\d+:\d+:\d+\.\d+),(\d+:\d+:\d+\.\d+),message_box,,.*$'
)

# ========== 可调参数 ==========
SCREEN_WIDTH        = 1080      # 视频宽度（像素）
SCREEN_HEIGHT       = 1920      # 视频高度（像素）
FONTSIZE            = 30        # 字体大小
SUPER_CHAT_DURATION = 60        # 每条SC的显示持续时间（秒）
BOX_WIDTH           = 360       # SC矩形宽度（像素）
BOX_TOP_HEIGHT      = 40        # 上框（用户名区）高度（像素）
LINE_HEIGHT         = FONTSIZE + 8  # 内容区每行高度 = 字体大小 + 行间距
PADDING_V           = 10        # 内容区上下内边距（像素）
CORNER_RADIUS       = 40        # 矩形圆角半径（像素）
MARGIN_LEFT         = 10        # SC距屏幕左边距（像素）
TEXT_PADDING        = 6         # 文字距矩形左/右边缘的内边距（像素）
BUFF                = 10        # 相邻SC之间的垂直间距（像素）
TOP_OPACITY         = 0.7       # 上框不透明度（0=全透明，1=不透明）
BOTTOM_OPACITY      = 0.3       # 下框不透明度（0=全透明，1=不透明）
NAME_BORDER         = False     # 用户名是否开启描边
NAME_BORDER_COLOR   = "FFFFFF"  # 用户名描边颜色（BGR十六进制）
NAME_BORDER_WIDTH   = 1         # 用户名描边宽度（像素）
CONTENT_BORDER      = True      # 内容文字是否开启描边
CONTENT_BORDER_COLOR= "000000"  # 内容描边颜色（BGR十六进制）
CONTENT_BORDER_WIDTH= 1         # 内容描边宽度（像素）
ANCHOR_Y_RATIO      = 0.8    # 新SC出现位置（屏幕高度的比例，3/4即距顶部75%处）比如 0.8 就更靠下，0.6 就更靠上
PUSH_DURATION_MS    = 300       # 被顶上去时的上移动画时长（毫秒）
SLIDE_DUR           = 0.5       # 滑入/滑出动画时长（秒）
# ==============================

SLIDE_START_X = -(BOX_WIDTH + MARGIN_LEFT)   # 矩形左边缘在屏幕外的x
TEXT_X0  = SLIDE_START_X + TEXT_PADDING       # 名字滑入起始x（左锚点）
TEXT_X1  = MARGIN_LEFT + TEXT_PADDING         # 名字静止x（左锚点）
PRICE_X0 = SLIDE_START_X + BOX_WIDTH - TEXT_PADDING  # 价格滑入起始x（右锚点）
PRICE_X1 = MARGIN_LEFT + BOX_WIDTH - TEXT_PADDING    # 价格静止x（右锚点）
ANCHOR_Y = int(SCREEN_HEIGHT * ANCHOR_Y_RATIO)
PUSH_DUR_S = PUSH_DURATION_MS / 1000.0


def hex_opacity(opacity):
    return hex(255 - int(opacity * 255))[2:].zfill(2)


def sec2hms(sec):
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return h, m, s


def fmt_time(sec):
    h, m, s = sec2hms(sec)
    return '%01d:%02d:%05.2f' % (h, m, s)


def hms2sec(t):
    h, m, s = t.split(':')
    return int(h) * 3600 + int(m) * 60 + float(s)


def calc_box_bottom_height(n_lines):
    return n_lines * LINE_HEIGHT + PADDING_V * 2


def parse_sc_groups(lines):
    """把所有 message_box 行按 (t0, t1) 分组，每组按4行切分（新格式）。"""
    time_groups = {}
    time_order = []
    for i, line in enumerate(lines):
        m = SC_LINE_RE.match(line.rstrip())
        if m:
            key = (m.group(1), m.group(2))
            if key not in time_groups:
                time_groups[key] = {'lines': [], 'indices': []}
                time_order.append(key)
            time_groups[key]['lines'].append(line.rstrip())
            time_groups[key]['indices'].append(i)

    sc_list = []
    for key in time_order:
        t0_str, t1_str = key
        grp_lines = time_groups[key]['lines']
        grp_idx   = time_groups[key]['indices']
        for start in range(0, len(grp_lines), 4):
            sub_lines   = grp_lines[start:start + 4]
            sub_indices = grp_idx[start:start + 4]
            if len(sub_lines) < 3:
                continue
            sc_list.append((t0_str, t1_str, sub_lines, sub_indices))

    return sc_list


def extract_sc_data(sub_lines):
    """
    从4行新格式中提取SC数据。
    sub_lines[0]: 上框（\p1，bg_color）
    sub_lines[1]: 下框（\p1，bg_bottom_color）
    sub_lines[2]: 名字行（\b1，name_color，文本="uname (price unit)"）
    sub_lines[3]: 内容行（\b1，content_color，文本=content）
    返回: (bg_color, bg_bottom_color, name_color, content_color,
            uname, price_text, content_text, n_lines)
    """
    bg_color        = 'EDF5FF'
    bg_bottom_color = '2A60B2'
    name_color      = 'FFFFFF'
    content_color   = 'FFFFFF'
    name_text       = ''
    content_text    = ''

    p1_idx = 0
    for line in sub_lines:
        cm = re.search(r'\\c&H([0-9A-Fa-f]{6})', line)
        if r'\p1' in line:
            if cm:
                if p1_idx == 0:
                    bg_color = cm.group(1)
                else:
                    bg_bottom_color = cm.group(1)
            p1_idx += 1
        elif r'\b1' in line:
            if cm:
                if not name_text:
                    name_color = cm.group(1)
                    m = re.search(r'\\q2\}(.+)$', line)
                    if m:
                        name_text = m.group(1).strip()
                else:
                    content_color = cm.group(1)
                    m = re.search(r'\\q2\}(.+)$', line)
                    if not m:
                        m = re.search(r'\}([^{]+)$', line)
                    if m:
                        content_text = m.group(1).strip()

    # 拆分 "uname (price_unit)" → uname, price_text
    uname = name_text
    price_text = ''
    pm = re.match(r'^(.+?) \(([^)]+)\)$', name_text)
    if pm:
        uname      = pm.group(1)
        price_text = pm.group(2)   # 例如 "100电池" 或 "30CNY"

    n_lines = max(1, content_text.count('\\N') + 1) if content_text else 1

    return bg_color, bg_bottom_color, name_color, content_color, \
           uname, price_text, content_text, n_lines


# ---------- segment type ----------
# 'entry'  : 首次出现，从左侧滑入
# 'push'   : 被新SC顶上去，原地向上移动
# 'static' : 静止停留
# 'exit'   : 自然消失，向左滑出


def finalize_segments(entries):
    """
    给每条SC的 segments 标注 seg_type，并在最后拆出 exit 段。
    segment格式: (seg_t0, seg_t1, y_start, y_end, seg_type)
    """
    for entry in entries:
        raw = entry['segments']   # [(t0, t1, ys, ye), ...]
        typed = []

        for i, (t0, t1, ys, ye) in enumerate(raw):
            is_first = (i == 0)
            is_last  = (i == len(raw) - 1)
            dur = t1 - t0

            if is_first and is_last:
                # 单段SC（从未被push），拆成 entry + exit
                if dur > SLIDE_DUR * 2:
                    typed.append((t0, t1 - SLIDE_DUR, ys, ye, 'entry'))
                    typed.append((t1 - SLIDE_DUR, t1, ye, ye, 'exit'))
                else:
                    typed.append((t0, t1, ys, ye, 'entry'))  # 太短，仅入场

            elif is_first:
                typed.append((t0, t1, ys, ye, 'entry'))

            elif is_last:
                if ys != ye:
                    # push段同时是最后一段：先push，再exit
                    push_end = t0 + PUSH_DUR_S
                    if push_end >= t1 - SLIDE_DUR:
                        # 时间太短，仅push
                        typed.append((t0, t1, ys, ye, 'push'))
                    else:
                        typed.append((t0, push_end, ys, ye, 'push'))
                        if t1 - push_end > SLIDE_DUR:
                            typed.append((push_end, t1 - SLIDE_DUR, ye, ye, 'static'))
                        typed.append((t1 - SLIDE_DUR, t1, ye, ye, 'exit'))
                else:
                    # 纯静止段是最后一段
                    if dur > SLIDE_DUR:
                        typed.append((t0, t1 - SLIDE_DUR, ys, ye, 'static'))
                        typed.append((t1 - SLIDE_DUR, t1, ye, ye, 'exit'))
                    else:
                        typed.append((t0, t1, ys, ye, 'exit'))

            else:
                # 中间段
                typed.append((t0, t1, ys, ye, 'push' if ys != ye else 'static'))

        entry['segments'] = typed


def build_sc_dialogue(t0, t1, y_segments, sc_data):
    """
    根据带类型的y坐标分段生成SC ASS行。
    y_segments: [(seg_t0, seg_t1, y_start, y_end, seg_type), ...]
    """
    bg_color, bg_bottom_color, name_color, content_color, \
        uname, price_text, content_text, n_lines = sc_data

    bh = calc_box_bottom_height(n_lines)

    top_opacity_hex    = hex_opacity(TOP_OPACITY)
    bottom_opacity_hex = hex_opacity(BOTTOM_OPACITY)
    name_border_tag    = f'\\bord{NAME_BORDER_WIDTH}\\3c&H{NAME_BORDER_COLOR}&' if NAME_BORDER else '\\bord0'
    content_border_tag = f'\\bord{CONTENT_BORDER_WIDTH}\\3c&H{CONTENT_BORDER_COLOR}&' if CONTENT_BORDER else '\\bord0'

    r  = CORNER_RADIUS
    w  = BOX_WIDTH
    th = BOX_TOP_HEIGHT

    result = []

    fad_map = {
        'entry' : '\\fad(500,0)',
        'push'  : '\\fad(0,0)',
        'static': '\\fad(0,0)',
        'exit'  : '\\fad(0,500)',
    }

    for seg_t0, seg_t1, y_start, y_end, seg_type in y_segments:
        seg_t0_fmt = fmt_time(seg_t0)
        seg_t1_fmt = fmt_time(seg_t1)
        fad = fad_map[seg_type]
        seg_dur_ms = int((seg_t1 - seg_t0) * 1000)
        d = PUSH_DURATION_MS

        if seg_type == 'entry':
            # 从屏幕左侧外滑入，y_start == y_end
            y = y_end
            top_move   = f'\\move({SLIDE_START_X},{y},{MARGIN_LEFT},{y},0,500)'
            top_move_b = f'\\move({SLIDE_START_X},{y+th},{MARGIN_LEFT},{y+th},0,500)'
            name_move  = f'\\an7\\move({TEXT_X0},{y+5},{TEXT_X1},{y+5},0,500)'
            price_move = f'\\an9\\move({PRICE_X0},{y+5},{PRICE_X1},{y+5},0,500)'
            cont_move  = f'\\move({TEXT_X0},{y+th+PADDING_V},{TEXT_X1},{y+th+PADDING_V},0,500)'

        elif seg_type == 'push':
            top_move   = f'\\move({MARGIN_LEFT},{y_start},{MARGIN_LEFT},{y_end},0,{d})'
            top_move_b = f'\\move({MARGIN_LEFT},{y_start+th},{MARGIN_LEFT},{y_end+th},0,{d})'
            name_move  = f'\\an7\\move({TEXT_X1},{y_start+5},{TEXT_X1},{y_end+5},0,{d})'
            price_move = f'\\an9\\move({PRICE_X1},{y_start+5},{PRICE_X1},{y_end+5},0,{d})'
            cont_move  = f'\\move({TEXT_X1},{y_start+th+PADDING_V},{TEXT_X1},{y_end+th+PADDING_V},0,{d})'

        elif seg_type == 'static':
            y = y_end
            top_move   = f'\\pos({MARGIN_LEFT},{y})'
            top_move_b = f'\\pos({MARGIN_LEFT},{y+th})'
            name_move  = f'\\an7\\pos({TEXT_X1},{y+5})'
            price_move = f'\\an9\\pos({PRICE_X1},{y+5})'
            cont_move  = f'\\pos({TEXT_X1},{y+th+PADDING_V})'

        elif seg_type == 'exit':
            # 向屏幕左侧外滑出，y_start == y_end
            y = y_end
            top_move   = f'\\move({MARGIN_LEFT},{y},{SLIDE_START_X},{y},0,500)'
            top_move_b = f'\\move({MARGIN_LEFT},{y+th},{SLIDE_START_X},{y+th},0,500)'
            name_move  = f'\\an7\\move({TEXT_X1},{y+5},{TEXT_X0},{y+5},0,500)'
            price_move = f'\\an9\\move({PRICE_X1},{y+5},{PRICE_X0},{y+5},0,500)'
            cont_move  = f'\\move({TEXT_X1},{y+th+PADDING_V},{TEXT_X0},{y+th+PADDING_V},0,500)'

        result.append(
            # 上框
            f'Dialogue: 0,{seg_t0_fmt},{seg_t1_fmt},message_box,,0,0,0,,'
            f'{{{fad}{top_move}\\c&H{bg_color}\\1a&H{top_opacity_hex}&\\shad0\\p1}}'
            f'm {r} 0 l {w-r} 0 b {w} 0 {w} 0 {w} {r} l {w} {th} l 0 {th} l 0 {r} b 0 0 0 0 {r} 0\n'
            # 下框
            f'Dialogue: 0,{seg_t0_fmt},{seg_t1_fmt},message_box,,0,0,0,,'
            f'{{{fad}{top_move_b}\\c&H{bg_bottom_color}\\1a&H{bottom_opacity_hex}&\\shad0\\p1}}'
            f'm 0 0 l {w} 0 l {w} {bh-r} b {w} {bh} {w} {bh} {w-r} {bh} l {r} {bh} b 0 {bh} 0 {bh} 0 {bh-r} l 0 0\n'
            # 用户名（左对齐）
            f'Dialogue: 1,{seg_t0_fmt},{seg_t1_fmt},message_box,,0,0,0,,'
            f'{{{fad}{name_move}\\c&H{name_color}\\fs{FONTSIZE}\\b1{name_border_tag}\\q2}}{uname}\n'
            # 价格（右对齐）
            f'Dialogue: 1,{seg_t0_fmt},{seg_t1_fmt},message_box,,0,0,0,,'
            f'{{{fad}{price_move}\\c&H{name_color}\\fs{FONTSIZE}\\b1{name_border_tag}\\q2}}{price_text}\n'
            # 内容
            f'Dialogue: 1,{seg_t0_fmt},{seg_t1_fmt},message_box,,0,0,0,,'
            f'{{{fad}{cont_move}\\c&H{content_color}\\fs{FONTSIZE}\\b1{content_border_tag}\\q2}}{content_text}\n'
        )

    return ''.join(result)


def compute_positions(sc_parsed_list):
    """
    预计算每条SC的y坐标分段（未带类型，finalize_segments负责类型化）。
    - 同时出现的SC强制排队，间隔1秒
    - 新SC固定出现在ANCHOR_Y，旧SC从下往上重新计算理想位置
    """
    active = []
    result = []
    last_effective_t0 = -9999.0

    for t0_str, t1_str, sub_lines, _ in sc_parsed_list:
        sc_data = extract_sc_data(sub_lines)
        uname, price_text, content_text = sc_data[4], sc_data[5], sc_data[6]
        if not uname or not content_text:
            print(f"  跳过（数据不完整）: t0={t0_str}")
            continue

        raw_t0 = hms2sec(t0_str)

        # 同时出现强制排队：间隔1秒
        if raw_t0 <= last_effective_t0:
            effective_t0 = last_effective_t0 + 1.0
        else:
            effective_t0 = raw_t0
        last_effective_t0 = effective_t0

        t1 = effective_t0 + SUPER_CHAT_DURATION

        # 清除已过期的SC
        active = [a for a in active if a['t1'] > effective_t0]

        # 从下往上重新计算每个活跃SC的理想位置
        available_bottom = ANCHOR_Y - BUFF  # 新SC上方留BUFF间距

        for a in sorted(active, key=lambda x: -x['current_y']):
            a_height = BOX_TOP_HEIGHT + calc_box_bottom_height(a['sc_data'][7])
            ideal_y  = available_bottom - a_height
            available_bottom = ideal_y - BUFF

            if ideal_y != a['current_y']:
                last = a['segments'][-1]
                a['segments'][-1] = (last[0], effective_t0, last[2], last[3])
                a['segments'].append((effective_t0, a['t1'], a['current_y'], ideal_y))
                a['current_y'] = ideal_y

        # 新SC出现在ANCHOR_Y
        entry = {
            't0'      : effective_t0,
            't1'      : t1,
            'sc_data' : sc_data,
            'current_y': ANCHOR_Y,
            'segments': [(effective_t0, t1, ANCHOR_Y, ANCHOR_Y)],
        }
        active.append(entry)
        result.append(entry)

    return result


def convert(input_path, output_path):
    with open(input_path, encoding='utf-8') as f:
        lines = f.readlines()

    sc_list = parse_sc_groups(lines)
    print(f"解析到 {len(sc_list)} 条 SuperChat")

    sc_indices = set()
    for _, _, _, sub_indices in sc_list:
        sc_indices.update(sub_indices)

    with open(output_path, 'w', encoding='utf-8') as f:
        for i, line in enumerate(lines):
            if i not in sc_indices:
                f.write(line)

    entries = compute_positions(sc_list)
    finalize_segments(entries)   # 标注类型，拆出 exit 段

    with open(output_path, 'a', encoding='utf-8') as f:
        for entry in entries:
            dialogue = build_sc_dialogue(
                entry['t0'], entry['t1'], entry['segments'], entry['sc_data']
            )
            f.write(dialogue)

    print(f"转换完成：{output_path}")
    print(f"共输出 {len(entries)} 条 SuperChat")


if __name__ == '__main__':
    input_path  = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_INPUT
    output_path = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_OUTPUT
    convert(input_path, output_path)
