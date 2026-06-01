"""
把ass文件中的旧格式SuperChat（5行/条）用add_super_chat重新生成新格式（4行/条，圆角矩形）
用法: python convert_sc.py [input.ass [output.ass]]
默认:
"""

import re
import sys
import os

DEFAULT_INPUT  = r"D:\DanmakuRender\Tasks文件\峰哥\test\5月31日23点32分.ass"
DEFAULT_OUTPUT = r"D:\DanmakuRender\Tasks文件\峰哥\test\test.ass"
# DEFAULT_INPUT  = r"D:\DanmakuRender\Tasks文件\峰哥\6月1日00点04分.ass"
# DEFAULT_OUTPUT = r"D:\DanmakuRender\Tasks文件\峰哥\6月1日00点04分_1.ass"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from DMR.Downloader.Danmaku.asswriter import AssWriter
from DMR.utils.danmaku import SuperChatDanmaku

SC_LINE_RE = re.compile(
    r'^Dialogue: \d+,(\d+:\d+:\d+\.\d+),(\d+:\d+:\d+\.\d+),message_box,,.*$'
)

def hms2sec(t):
    h, m, s = t.split(':')
    return int(h) * 3600 + int(m) * 60 + float(s)


def parse_sc_groups(lines):
    """
    把所有 message_box 行按 (t0, t1) 分组，保留原始顺序。
    每组内部再按每5行切分（旧格式每条SC固定5行）。
    返回: list of (t0_str, sub_group_lines, line_indices)
    """
    # 先收集所有 message_box 行，按 (t0,t1) 分组
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

    # 每组内按5行切分
    sc_list = []  # list of (t0_str, t1_str, sub_lines, sub_indices)
    for key in time_order:
        t0_str, t1_str = key
        grp_lines = time_groups[key]['lines']
        grp_idx   = time_groups[key]['indices']
        # 每5行一条SC
        for start in range(0, len(grp_lines), 5):
            sub_lines   = grp_lines[start:start + 5]
            sub_indices = grp_idx[start:start + 5]
            if len(sub_lines) < 3:
                continue
            sc_list.append((t0_str, t1_str, sub_lines, sub_indices))

    return sc_list


def extract_sc_data(sub_lines):
    """
    从5行旧格式中提取 SC 字段。
    旧格式:
      行1/2: 绘图框 (\p1)
      行3:   uname (\b1)
      行4:   price (SuperChat CNY X)
      行5:   content (其余文字行)
    """
    uname = price = content = None
    price_unit = 'CNY'

    for line in sub_lines:
        if r'\p1' in line:
            pass  # 绘图框，跳过

        elif r'\b1' in line:
            # uname 行
            m = re.search(r'\\q2\}(.+)$', line)
            if m:
                uname = m.group(1).strip()

        elif 'SuperChat CNY' in line:
            # price 行: "SuperChat CNY 30"
            mp = re.search(r'SuperChat\s+(\S+)\s+(\S+)', line)
            if mp:
                price_unit = mp.group(1)
                price = mp.group(2)

        else:
            # content 行
            m = re.search(r'\\q2\}(.+)$', line)
            if not m:
                m = re.search(r'\}([^{]+)$', line)
            if m:
                content = m.group(1).strip().replace('\\N', ' ')

    return uname, price, price_unit, content


def convert(input_path, output_path):
    with open(input_path, encoding='utf-8') as f:
        lines = f.readlines()

    sc_list = parse_sc_groups(lines)

    # 收集所有 SC 行号
    sc_indices = set()
    for _, _, _, sub_indices in sc_list:
        sc_indices.update(sub_indices)

    # 把非 SC 行写入输出文件
    with open(output_path, 'w', encoding='utf-8') as f:
        for i, line in enumerate(lines):
            if i not in sc_indices:
                f.write(line)

    # 创建 AssWriter（仅借用 add_super_chat，其余参数不影响 SC 渲染）
    writer = AssWriter(
        description='', width=1080, height=1920, dst=0, dmrate=1.0,
        font='Microsoft YaHei', fontsize=30, margin_h=0, margin_w=0,
        dmduration=10, opacity=0.0, auto_fontsize=False,
        outlinecolor='000000', outlinesize=1,
    )
    writer._filename = output_path

    sc_count = 0
    skipped  = 0
    for t0_str, t1_str, sub_lines, _ in sc_list:
        uname, price, price_unit, content = extract_sc_data(sub_lines)
        if not all([uname, price, content]):
            print(f"  跳过（数据不完整）: t0={t0_str} uname={uname!r} price={price!r} content={content!r}")
            skipped += 1
            continue
        sc = SuperChatDanmaku(
            price=price,
            price_unit=price_unit,
            name=uname,
            time=hms2sec(t0_str),
            content=content,
            uname=uname,
            color='ffffff',
        )
        writer.add_super_chat(sc)
        sc_count += 1

    print(f"转换完成：{output_path}")
    print(f"共处理 {sc_count} 条 SuperChat，跳过 {skipped} 条")


if __name__ == '__main__':
    input_path  = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_INPUT
    output_path = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_OUTPUT
    convert(input_path, output_path)
