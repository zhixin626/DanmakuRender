"""
字幕后端核心逻辑（无 GUI 依赖）：ASS 读写、txt->ass 生成、ffmpeg 渲染、
调 run_asr 语音识别、颜色/时间转换、换行、字体枚举。

供 subtitle_editor_qt.py（GUI）和命令行/DMR 复用。

命令行用法（对视频做 ASR 并产出字幕 ass，打印 ass 路径）：
    python subtitle_core.py "D:\\x\\视频.mp4"
    python subtitle_core.py "D:\\x\\视频.mp4" --size 60 --margin 50 --wrap 20 \
        --font "微软雅黑" --font-color "#FFD500" --outline 6 --outline-color "#000000" \
        --out "视频(字幕).ass"
依赖：同目录 run_asr.py、系统 PATH 里的 ffmpeg/ffprobe。
"""

import os
import re
import sys
import json
import threading
import subprocess

OVERWRITE_OUTPUT = True   # 渲染输出默认直接覆盖同名；False 则自动改名避免重名


def temp_dir():
    """字幕编辑器的临时文件目录：放在程序目录下的 temp/（而非系统临时目录），便于查看/清理。"""
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'temp')
    os.makedirs(d, exist_ok=True)
    return d


def clear_temp(keep=()):
    """清空临时文件夹（删其中所有文件/子目录），返回删除的条目数。
    keep：要跳过的绝对路径集合（如正在使用的预览文件）。"""
    import shutil
    keepset = {os.path.normcase(os.path.abspath(p)) for p in (keep or []) if p}
    d = temp_dir()
    n = 0
    for name in os.listdir(d):
        fp = os.path.join(d, name)
        if os.path.normcase(os.path.abspath(fp)) in keepset:
            continue
        try:
            if os.path.isdir(fp):
                shutil.rmtree(fp, ignore_errors=True)
            else:
                os.remove(fp)
            n += 1
        except Exception:
            pass
    return n

# 从 txt 首次生成 ass 时用的默认样式（之后都以 ass 为准）
DEFAULT_STYLE = {
    'font_name':     '文悦新青年体 (须授权) W8-J',
    'font_size':     70,          # 像素，基于视频真实分辨率
    'margin_bottom': 0.06,        # 距下边缘，比例 0~1（0.06=6%，基于视频高度，写 ass 时换算成像素）
    'font_color':    '#FFD500',
    'outline':       6,
    'outline_color': '#000000',
    'wrap_chars':    24,          # 每行最多字数，超过则插入换行（0=不换行）
}

VIDEO_PATH = None   # 当前工作视频（由 set_video 设置）
TXT_PATH = None
ASS_PATH = None

# 后台任务（语音识别）状态
task_state = {'running': False, 'done': False, 'error': None, 'msg': '', 'percent': -1}

_TXT_RE = re.compile(r'\[(\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?) --> (\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?)\] (spk_\w+): (.+)')


def set_video(path):
    """切换当前工作视频，同步推导出同名的 txt / ass 路径。"""
    global VIDEO_PATH, TXT_PATH, ASS_PATH
    VIDEO_PATH = path
    TXT_PATH = os.path.splitext(path)[0] + '.txt'
    ASS_PATH = os.path.splitext(path)[0] + '.ass'


def apply_import(path):
    """切换工作视频；有 txt 无 ass 时顺手生成 ass（不识别）。"""
    set_video(path)
    if not os.path.exists(ASS_PATH) and os.path.exists(TXT_PATH):
        gen_from_txt()


# ── 语音识别（调 run_asr.py 子进程）──────────────────────────────────────────

def run_asr_subprocess(show_output=False, on_proc=None):
    """调用 run_asr.py 做语音识别。on_proc(Popen) 可选：拿到子进程句柄以便外部中止。
    show_output=True：继承终端，FunASR/tqdm 进度直接打印到命令行（失败时不另外回传尾部）。
    show_output=False：吞掉输出保持命令行干净，失败时回传错误尾部给上层显示。"""
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'run_asr.py')
    # 第二参数=输出 txt 路径，让 run_asr 写到 TXT_PATH（支持不覆盖时的唯一命名）
    cmd = [sys.executable, '-u', script, VIDEO_PATH, TXT_PATH]
    if show_output:
        p = subprocess.Popen(cmd)
        if on_proc:
            on_proc(p)
        p.wait()
        if p.returncode != 0:
            raise RuntimeError(f"run_asr 失败（退出码 {p.returncode}），详见命令行输出")
    else:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding='utf-8', errors='ignore')
        if on_proc:
            on_proc(p)
        out = p.communicate()[0]
        if p.returncode != 0:
            tail = (out or '')[-2000:]
            raise RuntimeError(f"run_asr 失败（退出码 {p.returncode}）\n{tail}")


# ── 颜色 / 时间转换 ──────────────────────────────────────────────────────────

def hex_to_ass(h):
    """#RRGGBB 或 #AARRGGBB -> ASS 的 &HAABBGGRR"""
    h = h.lstrip('#')
    if len(h) == 6:
        aa, rr, gg, bb = '00', h[0:2], h[2:4], h[4:6]
    elif len(h) == 8:
        aa, rr, gg, bb = h[0:2], h[2:4], h[4:6], h[6:8]
    else:
        raise ValueError(f"颜色格式错误：{h}")
    return f"&H{aa}{bb}{gg}{rr}".upper()


def ass_to_hex(a):
    """ASS &HAABBGGRR -> #RRGGBB"""
    a = a.replace('&H', '').replace('&h', '').strip().zfill(8)
    _, bb, gg, rr = a[0:2], a[2:4], a[4:6], a[6:8]
    return f"#{rr}{gg}{bb}".upper()


def ms_to_ass(ms):
    h, r = divmod(int(ms), 3_600_000)
    m, r = divmod(r, 60_000)
    s, r = divmod(r, 1_000)
    return f"{h}:{m:02d}:{s:02d}.{r//10:02d}"


def ass_to_ms(t):
    h, m, rest = t.split(':')
    s, cs = rest.split('.')
    return int(h)*3_600_000 + int(m)*60_000 + int(s)*1_000 + int(cs)*10


def _txt_hms_to_ms(hms):
    h, m, s = hms.split(':')
    return int(h)*3_600_000 + int(m)*60_000 + int(round(float(s)*1000))


def remove_spaces(text):
    """去掉整句话里的所有空白字符（空格、制表符、换行等）。"""
    return re.sub(r'\s+', '', text)


def _char_width(ch):
    """字符显示宽度：英文/数字/英文标点等 ASCII 字符算半个(0.5)，中文等全角字符算一个(1.0)。"""
    return 0.5 if ord(ch) < 128 else 1.0


# 字符计算规则的说明文字，供界面提示用户「字数是怎么算出来的」
CHAR_WIDTH_DESC = "字数按显示宽度计：中文等全角字符算 1，英文/数字/英文标点算 0.5"


def char_width_desc():
    """返回当前字符计算规则的说明（与 _char_width 的算法保持一致）。"""
    return CHAR_WIDTH_DESC


def text_width(s):
    """整段文本的显示宽度合计（按 _char_width 累加），中文算 1、英文算 0.5。"""
    return sum(_char_width(c) for c in s)


def wrap_text(text, n, strip_spaces=True):
    """按显示宽度重排：每行累计宽度不超过 n（中文算 1，英文算 0.5）。
    strip_spaces=True（默认）先调 remove_spaces 去掉整句所有空格；
    strip_spaces=False 则保留空格（仅去换行、合并多余空格）。
    n<=0 时不重排，只做空格处理。"""
    s = text.replace('\n', '')
    if strip_spaces:
        s = remove_spaces(s)
    else:
        s = re.sub(r' {2,}', ' ', s).strip()   # 合并多余空格 + 去首尾
    if n <= 0:
        return s
    lines, cur, w = [], [], 0.0
    for ch in s:
        cw = _char_width(ch)
        if cur and w + cw > n:        # 再加这个字会超宽 → 换行
            lines.append(''.join(cur))
            cur, w = [], 0.0
        cur.append(ch); w += cw
    if cur:
        lines.append(''.join(cur))
    if not strip_spaces:
        lines = [ln.strip() for ln in lines]   # 保留空格时去掉行首尾残留空格
    return '\n'.join(lines)


# ── ffprobe / 字体 ───────────────────────────────────────────────────────────

def video_size(path):
    out = subprocess.run(
        ['ffprobe', '-v', 'error', '-select_streams', 'v:0',
         '-show_entries', 'stream=width,height', '-of', 'json', path],
        capture_output=True, text=True, encoding='utf-8')
    s = json.loads(out.stdout)['streams'][0]
    return int(s['width']), int(s['height'])


def video_duration_ms(path):
    out = subprocess.run(
        ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
         '-of', 'json', path],
        capture_output=True, text=True, encoding='utf-8')
    return int(float(json.loads(out.stdout)['format']['duration']) * 1000)


_FONTS_CACHE = None

def installed_fonts():
    """枚举系统已安装字体的家族名（fontconfig/ffmpeg 按此名匹配）。"""
    global _FONTS_CACHE
    if _FONTS_CACHE is not None:
        return _FONTS_CACHE
    try:
        ps = ("[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
              "Add-Type -AssemblyName System.Drawing;"
              "(New-Object System.Drawing.Text.InstalledFontCollection).Families | "
              "ForEach-Object { $_.Name }")
        out = subprocess.run(['powershell', '-NoProfile', '-Command', ps],
                             capture_output=True, text=True, encoding='utf-8', errors='ignore')
        _FONTS_CACHE = sorted({l.strip() for l in out.stdout.splitlines() if l.strip()})
    except Exception:
        _FONTS_CACHE = []
    return _FONTS_CACHE


# ── ASS 读写 ─────────────────────────────────────────────────────────────────

def build_ass(style, events, play_w, play_h):
    s = style
    marginv = round(s['margin_bottom'] * play_h)   # 比例(0~1) -> 像素(基于视频高度)
    style_line = (
        f"Style: Default,{s['font_name']},{s['font_size']},"
        f"{hex_to_ass(s['font_color'])},&H000000FF,"
        f"{hex_to_ass(s['outline_color'])},&H80000000,"
        f"0,0,0,0,100,100,0,0,1,{s['outline']},0,2,10,10,{marginv},1"
    )
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {play_w}",
        f"PlayResY: {play_h}",
        "WrapStyle: 2",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        style_line,
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for e in events:
        text = e['text'].replace('\n', '\\n')   # 软换行 \n（受 WrapStyle 影响），非强制 \N
        # Name 列留空：成品字幕不需要 spk_/人1人2（ASR 的说话人编号对渲染无意义）
        lines.append(
            f"Dialogue: 0,{ms_to_ass(e['start'])},{ms_to_ass(e['end'])},Default,,0,0,0,,{text}")
    return "\n".join(lines) + "\n"


def write_ass(style, events, play_w, play_h, path=None):
    if path is None:
        path = ASS_PATH
    with open(path, 'w', encoding='utf-8-sig') as f:
        f.write(build_ass(style, events, play_w, play_h))


def parse_ass(path=None):
    if path is None:
        path = ASS_PATH
    style = dict(DEFAULT_STYLE)
    events = []
    play_w = play_h = None
    style_fmt = event_fmt = None
    with open(path, encoding='utf-8-sig') as f:
        for raw in f:
            line = raw.strip()
            if line.startswith('PlayResX:'):
                play_w = int(line.split(':', 1)[1])
            elif line.startswith('PlayResY:'):
                play_h = int(line.split(':', 1)[1])
            elif line.startswith('Format:'):
                fmt = [c.strip().lower() for c in line.split(':', 1)[1].split(',')]
                if 'text' in fmt:      # Events 的 Format 含 'text'，靠这个区分两段
                    event_fmt = fmt
                else:
                    style_fmt = fmt
            elif line.startswith('Style:') and style_fmt:
                vals = [v.strip() for v in line.split(':', 1)[1].split(',')]
                d = dict(zip(style_fmt, vals))
                style = {
                    'font_name':     d.get('fontname', DEFAULT_STYLE['font_name']),
                    'font_size':     int(float(d.get('fontsize', DEFAULT_STYLE['font_size']))),
                    'margin_bottom': (round(float(d['marginv']) / (play_h or 1080), 4)   # 像素 -> 比例(0~1)
                                      if d.get('marginv') is not None else DEFAULT_STYLE['margin_bottom']),
                    'font_color':    ass_to_hex(d.get('primarycolour', '&H0000FFFF')),
                    'outline':       float(d.get('outline', DEFAULT_STYLE['outline'])),
                    'outline_color': ass_to_hex(d.get('outlinecolour', '&H00000000')),
                    'wrap_chars':    DEFAULT_STYLE['wrap_chars'],   # ASS 不存此项，沿用默认
                }
            elif line.startswith('Dialogue:') and event_fmt:
                vals = line.split(':', 1)[1].split(',', len(event_fmt) - 1)
                d = dict(zip(event_fmt, [v.strip() for v in vals]))
                events.append({
                    'start': ass_to_ms(d['start']),
                    'end':   ass_to_ms(d['end']),
                    'name':  d.get('name', ''),
                    'text':  d.get('text', '').replace('\\N', '\n').replace('\\n', '\n'),
                })
    if not play_w or not play_h:
        play_w, play_h = video_size(VIDEO_PATH)
    return style, events, play_w, play_h


def _style_vals_from_fields(d, play_h):
    """Style 行解析出的 {fmt列名:值} -> 工具栏样式 dict（字体/字号/下边距/颜色/描边/换行）。"""
    return {
        'font_name':     d.get('fontname', DEFAULT_STYLE['font_name']),
        'font_size':     int(float(d.get('fontsize', DEFAULT_STYLE['font_size']))),
        'margin_bottom': (round(float(d['marginv']) / (play_h or 1080), 4)   # 像素 -> 比例(0~1)
                          if d.get('marginv') not in (None, '') else DEFAULT_STYLE['margin_bottom']),
        'font_color':    ass_to_hex(d.get('primarycolour', '&H0000FFFF')),
        'outline':       float(d.get('outline', DEFAULT_STYLE['outline'])),
        'outline_color': ass_to_hex(d.get('outlinecolour', '&H00000000')),
        'wrap_chars':    DEFAULT_STYLE['wrap_chars'],   # ASS 不存此项，沿用默认
    }


def parse_ass_full(path):
    """完整解析 ass，供统一编辑器使用：保留每条 Dialogue 的全部字段与原始顺序、所有 Style。
    返回 (play_w, play_h, styles, dialogues)：
      styles    : {style_name: 工具栏样式 dict}（按文件顺序）
      dialogues : [dict(layer,start,end,style,name,marginl,marginr,marginv,effect,text), ...]
                  start/end 为 ms(int)；text 为文件原文（含 {\\...} 标签与 \\N，不做任何替换）
    """
    play_w = play_h = None
    style_fmt = event_fmt = None
    styles, dialogues = {}, []
    with open(path, encoding='utf-8-sig') as f:
        for raw in f:
            line = raw.strip()
            if line.startswith('PlayResX:'):
                try: play_w = int(line.split(':', 1)[1])
                except ValueError: pass
            elif line.startswith('PlayResY:'):
                try: play_h = int(line.split(':', 1)[1])
                except ValueError: pass
            elif line.startswith('Format:'):
                fmt = [c.strip().lower() for c in line.split(':', 1)[1].split(',')]
                if 'text' in fmt:
                    event_fmt = fmt
                else:
                    style_fmt = fmt
            elif line.startswith('Style:') and style_fmt:
                vals = [v.strip() for v in line.split(':', 1)[1].split(',')]
                d = dict(zip(style_fmt, vals))
                styles[d.get('name', 'Default')] = _style_vals_from_fields(d, play_h)
            elif line.startswith('Dialogue:') and event_fmt:
                vals = line.split(':', 1)[1].split(',', len(event_fmt) - 1)   # text 保留逗号
                d = dict(zip(event_fmt, vals))
                try:
                    s = ass_to_ms(d['start'].strip()); e = ass_to_ms(d['end'].strip())
                except Exception:
                    continue
                dialogues.append({
                    'layer':   (d.get('layer') or '0').strip(),
                    'start':   s, 'end': e,
                    'style':   (d.get('style') or 'Default').strip(),
                    'name':    (d.get('name') or '').strip(),
                    'marginl': (d.get('marginl') or '0').strip(),
                    'marginr': (d.get('marginr') or '0').strip(),
                    'marginv': (d.get('marginv') or '0').strip(),
                    'effect':  (d.get('effect') or '').strip(),
                    'text':    d.get('text', ''),
                })
    return play_w, play_h, styles, dialogues


def dialogue_line(d):
    """统一编辑器的 Dialogue dict -> 一行 ass 文本（不含结尾换行）。"""
    return (f"Dialogue: {d['layer']},{ms_to_ass(d['start'])},{ms_to_ass(d['end'])},"
            f"{d['style']},{d['name']},{d['marginl']},{d['marginr']},{d['marginv']},"
            f"{d['effect']},{d['text']}")


def write_dialogues_to_ass(path, dialogues):
    """把 dialogues 整体写回 path：保留所有非 Dialogue 行（头部/Style/注释如 GIFT_DATA），
    在原首条 Dialogue 位置按顺序写出全部 dialogues，其余原 Dialogue 跳过——
    与逐行编辑配套，删/增/改同步到文件而不破坏样式与头部。"""
    try:
        with open(path, encoding='utf-8-sig') as f:
            lines = f.readlines()
    except Exception:
        return False
    new = [dialogue_line(d) + '\n' for d in dialogues]
    out, dumped = [], False
    for line in lines:
        if line.startswith('Dialogue:'):
            if not dumped:
                out.extend(new); dumped = True
            continue
        out.append(line)
    if not dumped:
        if out and not out[-1].endswith('\n'):
            out[-1] += '\n'
        out.extend(new)
    with open(path, 'w', encoding='utf-8-sig') as f:
        f.writelines(out)
    return True


# ── 统一编辑器：样式枚举 / 就地改 Style / inline 覆盖分析 ───────────────────────
# 思路：把"字幕"与"弹幕"统一为同一种处理——以原始 ass 行为准，工具栏只就地 patch
# [V4+ Styles] 里选中的那条 Style，逐行编辑只动对应 Dialogue。哪些工具栏参数对某条
# Style 真正有效，不靠样式名硬编码，而是扫描该 Style 名下所有 Dialogue 的 inline
# 覆盖标签自动推断（analyze_style_overrides）。

# 工具栏参数 -> 判定"被逐行 inline 覆盖"的标签正则
_OVERRIDE_PATTERNS = {
    'font_name':     re.compile(r'\\fn'),            # \fn 改字体
    'font_size':     re.compile(r'\\fs\d'),          # \fs 改字号（排除 \fscx/\fscy/\fsp）
    'font_color':    re.compile(r'\\1?c&'),          # \c& 或 \1c& 改主色（不匹配 \3c&）
    'outline':       re.compile(r'\\bord'),          # \bord 改描边宽
    'outline_color': re.compile(r'\\3c&'),           # \3c& 改描边色
    'margin_bottom': re.compile(r'\\pos|\\move|\\an\d|\\a\d'),  # 绝对定位 → MarginV/对齐失效
}

# 与 _OVERRIDE_PATTERNS 同序的工具栏参数键（UI 按此置灰/提示）
STYLE_PARAMS = tuple(_OVERRIDE_PATTERNS.keys())


def analyze_style_overrides(texts):
    """给定使用同一条 Style 的所有 Dialogue 的 Text 字段列表，判定每个工具栏参数
    是否被 inline 标签覆盖（与样式名无关，纯数据驱动，自动适配未知/自建 Style）。

    返回 {param: 状态}，状态取值：
      'free'   —— 没有任何行覆盖该参数：改 Style 全部生效
      'partial'—— 仅部分行覆盖：改 Style 只影响其余未覆盖的行（UI 应保留可编辑但警告）
      'locked' —— 所有行都覆盖：改 Style 对它无效（UI 应置灰）
    texts 为空（该 Style 暂无 Dialogue）时一律返回 'free'。
    """
    n = len(texts)
    result = {}
    for param, pat in _OVERRIDE_PATTERNS.items():
        if n == 0:
            result[param] = 'free'
            continue
        hits = sum(1 for t in texts if pat.search(t or ''))
        result[param] = 'free' if hits == 0 else ('locked' if hits == n else 'partial')
    return result


def list_styles(path):
    """枚举 ass 的 [V4+ Styles] 段里所有 Style 的名字（按文件顺序），供样式下拉使用。"""
    names = []
    style_fmt = None
    try:
        with open(path, encoding='utf-8-sig') as f:
            for raw in f:
                line = raw.strip()
                if line.startswith('Format:'):
                    fmt = [c.strip().lower() for c in line.split(':', 1)[1].split(',')]
                    if 'text' not in fmt:          # styles 段的 Format（不含 text）
                        style_fmt = fmt
                elif line.startswith('Style:') and style_fmt:
                    vals = [v.strip() for v in line.split(':', 1)[1].split(',')]
                    ni = style_fmt.index('name') if 'name' in style_fmt else 0
                    if ni < len(vals):
                        names.append(vals[ni])
    except Exception:
        pass
    return names


def patch_style_in_ass(path, style_name, style, play_h=None):
    """就地修改 path 中名为 style_name 的那条 Style 行（字体/字号/下边距/描边/字色/描边色），
    其余所有行（其它 Style、全部 Dialogue、头部注释）原样保留——这是"工具栏=全局改 Style"的写回机制，
    取代 build_ass 的整文件重建，对多样式/含 inline 标签的弹幕 ass 也安全。

    style：DEFAULT_STYLE 结构的 dict，只用到 6 个键；只 patch 该 Style 行里实际存在的列。
    play_h：把 margin_bottom(比例 0~1) 换算成 MarginV 像素；None 时从文件 PlayResY 读取。
    返回是否成功 patch 到目标 Style。
    """
    try:
        with open(path, encoding='utf-8-sig') as f:
            lines = f.readlines()
    except Exception:
        return False

    style_fmt = None
    for raw in lines:
        line = raw.strip()
        if play_h is None and line.startswith('PlayResY:'):
            try:
                play_h = int(line.split(':', 1)[1])
            except ValueError:
                pass
        if line.startswith('Format:'):
            fmt = [c.strip().lower() for c in line.split(':', 1)[1].split(',')]
            if 'text' not in fmt:
                style_fmt = fmt
    if not style_fmt or 'name' not in style_fmt:
        return False
    idx = {k: i for i, k in enumerate(style_fmt)}

    # fmt 列名 -> 新值（仅当该列存在于本文件的 Format 中才写）
    updates = {}
    if 'fontname' in idx:      updates['fontname'] = str(style['font_name'])
    if 'fontsize' in idx:      updates['fontsize'] = str(int(style['font_size']))
    if 'outline' in idx:       updates['outline'] = f"{float(style['outline']):g}"
    if 'primarycolour' in idx: updates['primarycolour'] = hex_to_ass(style['font_color'])
    if 'outlinecolour' in idx: updates['outlinecolour'] = hex_to_ass(style['outline_color'])
    if 'marginv' in idx and play_h:
        updates['marginv'] = str(round(style['margin_bottom'] * play_h))

    ni = idx['name']
    out, patched = [], False
    for raw in lines:
        line = raw.strip()
        if (not patched) and line.startswith('Style:'):
            vals = [v.strip() for v in line.split(':', 1)[1].split(',')]
            if ni < len(vals) and vals[ni] == style_name:
                for k, v in updates.items():
                    if idx[k] < len(vals):
                        vals[idx[k]] = v
                out.append('Style: ' + ','.join(vals) + '\n')
                patched = True
                continue
        out.append(raw)
    if patched:
        with open(path, 'w', encoding='utf-8-sig') as f:
            f.writelines(out)
    return patched


# ── 单行弹幕内联样式标签的读写（\c / \1c 填充色、\1a 不透明度、\bord 描边、\3c 描边色）──────
# 颜色按整数解析（鲁棒于 &Hff& 这类短写），统一 RGB(#RRGGBB) <-> BGR(&HBBGGRR&)；
# 不透明度用直觉的"百分比 0~100（100=完全可见）"，与 ASS 的 &HAA&（00=不透明,FF=全透明）互转。

# 颜色/alpha 的结尾 & 是可选的：弹幕里常见 \c&H737373\fs30（用下一个 \ 收尾，无结尾 &），
# 也有 \3c&H000000&（带结尾 &）。十六进制是贪婪匹配，遇到非 hex（\ 或 } 或 &）即止。
_RE_C    = re.compile(r'\\1?c&[Hh]([0-9A-Fa-f]+)&?')         # \c 或 \1c（&H/&h 都认；不误匹配 \3c）
_RE_3C   = re.compile(r'\\3c&[Hh]([0-9A-Fa-f]+)&?')           # \3c
_RE_A    = re.compile(r'\\(?:1a|alpha)&[Hh]([0-9A-Fa-f]+)&?') # \1a 或长写 \alpha（&H/&h 都认）
_RE_BORD = re.compile(r'\\bord(\d+(?:\.\d+)?)')            # \bord


def inline_color_to_hex(s):
    """ASS 内联色 &HBBGGRR&（可能短写）-> #RRGGBB。按整数解析，缺位也不会错位。"""
    s = s.strip().lstrip('&').lstrip('Hh').rstrip('&')
    try:
        v = int(s or '0', 16)
    except ValueError:
        return '#FFFFFF'
    r, g, b = v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF
    return f"#{r:02X}{g:02X}{b:02X}"


def hex_to_inline_color(h):
    """#RRGGBB -> ASS 内联色 &HBBGGRR&（BGR 倒序）。"""
    h = h.lstrip('#')
    rr, gg, bb = h[0:2], h[2:4], h[4:6]
    return f"&H{bb}{gg}{rr}&".upper()


def alpha_to_opacity(s):
    """ASS 内联 alpha &HAA&（00=不透明,FF=全透明）-> 不透明度百分比 0~100。"""
    s = s.strip().lstrip('&').lstrip('Hh').rstrip('&')
    try:
        aa = int(s or '0', 16) & 0xFF
    except ValueError:
        aa = 0
    return round((255 - aa) / 255 * 100)


def opacity_to_alpha(pct):
    """不透明度百分比 0~100 -> ASS 内联 alpha &HAA&。"""
    pct = max(0, min(100, pct))
    aa = round((100 - pct) / 100 * 255)
    return f"&H{aa:02X}&"


def parse_inline_style(text):
    """从弹幕 Text 解析当前内联样式。值为 None 表示该标签当前不存在。
    返回 {'fill': '#RRGGBB'|None, 'opacity': 0~100|None, 'bord': float|None, 'outline_color': '#RRGGBB'|None}"""
    res = {'fill': None, 'opacity': None, 'bord': None, 'outline_color': None}
    m = _RE_C.search(text)
    if m:
        res['fill'] = inline_color_to_hex(m.group(1))
    m = _RE_A.search(text)
    if m:
        res['opacity'] = alpha_to_opacity(m.group(1))
    m = _RE_BORD.search(text)
    if m:
        res['bord'] = float(m.group(1))
    m = _RE_3C.search(text)
    if m:
        res['outline_color'] = inline_color_to_hex(m.group(1))
    return res


def apply_inline_style(text, fill=('keep',), opacity=('keep',), bord=('keep',), outline_color=('keep',)):
    """增改/删除弹幕 Text 里的内联样式标签，其余原样保留。只动这一行。
    标签可能分散在多个 {} 块里（如 {\\move...}{\\alpha..\\1c..}文本）：增改/删除在整段就地进行，
    只有"插入一个原本不存在的标签"时才放进第一个 {} 块。每个参数是动作元组：
      fill / outline_color : ('set', '#RRGGBB') | ('remove',) | ('keep',)
      opacity              : ('set', 0~100)     | ('remove',) | ('keep',)
      bord                 : ('on', 宽度)        | ('off',)    | ('keep',)   # off 显式写 \\bord0
    """
    def insert_first_block(text, newtag):
        """把全新标签插进第一个 {} 块（紧贴右花括号前）；没有块则在正文最前新建一个块。"""
        if text.startswith('{') and '}' in text:
            i = text.index('}')
            return text[:i] + newtag + text[i:]
        return '{' + newtag + '}' + text

    def set_tag(text, pat, newtag):
        # 标签可能在任意一个 {} 块里 → 整段就地替换；不存在才插进第一个块。
        if pat.search(text):
            return pat.sub(lambda m: newtag, text, count=1)   # 用函数避免反斜杠转义问题
        return insert_first_block(text, newtag)

    def form_of(m, default):
        return m.group(0)[:m.group(0).index('&')] if m else default

    # 填充色（保留原写法 \c / \1c）
    if fill[0] == 'set':
        text = set_tag(text, _RE_C, form_of(_RE_C.search(text), r'\c') + hex_to_inline_color(fill[1]))
    elif fill[0] == 'remove':
        text = _RE_C.sub('', text)

    # 不透明度（保留原写法 \1a / \alpha）
    if opacity[0] == 'set':
        text = set_tag(text, _RE_A, form_of(_RE_A.search(text), r'\1a') + opacity_to_alpha(opacity[1]))
    elif opacity[0] == 'remove':
        text = _RE_A.sub('', text)

    # 描边色
    if outline_color[0] == 'set':
        text = set_tag(text, _RE_3C, r'\3c' + hex_to_inline_color(outline_color[1]))
    elif outline_color[0] == 'remove':
        text = _RE_3C.sub('', text)

    # 描边开关：on→\bord<宽度>，off→\bord0（都写）
    if bord[0] in ('on', 'off'):
        w = bord[1] if bord[0] == 'on' else 0
        text = set_tag(text, _RE_BORD, r'\bord' + f"{float(w):g}")

    return text


# ── \move 的读写（起点终点坐标 + 可选移动时间）─────────────────────────────────
# \move(x1,y1,x2,y2) 或 \move(x1,y1,x2,y2,t1,t2)；t1/t2 是相对本行开始的毫秒。
_RE_MOVE = re.compile(r'\\move\(([^)]*)\)')


def parse_move(text):
    """解析第一个 \\move(...)。返回 {'x1','y1','x2','y2','t1','t2'}（int；t1/t2 为 None 表示 4 参形式）；
    无 \\move 返回 None。"""
    m = _RE_MOVE.search(text)
    if not m:
        return None
    parts = [p.strip() for p in m.group(1).split(',')]
    if len(parts) < 4:
        return None

    def num(s):
        try:
            return int(round(float(s)))
        except ValueError:
            return 0
    d = {'x1': num(parts[0]), 'y1': num(parts[1]), 'x2': num(parts[2]), 'y2': num(parts[3]),
         't1': None, 't2': None}
    if len(parts) >= 6:
        d['t1'], d['t2'] = num(parts[4]), num(parts[5])
    return d


def apply_move(text, x1, y1, x2, y2, t1=None, t2=None):
    """改写第一个 \\move(...)。t1/t2 同时给出则写 6 参（带时间），否则写 4 参。其余原样保留。"""
    def f(v):
        return str(int(round(v)))
    if t1 is not None and t2 is not None:
        new = f"\\move({f(x1)},{f(y1)},{f(x2)},{f(y2)},{f(t1)},{f(t2)})"
    else:
        new = f"\\move({f(x1)},{f(y1)},{f(x2)},{f(y2)})"
    return _RE_MOVE.sub(lambda m: new, text, count=1)


def merge_ass_for_preview(editable_path, ref_path, out_path):
    """把「参考 ass(背景，如弹幕)」与「正在编辑的 ass(字幕)」合并成一个临时 ass，仅供 mpv 预览。
    以参考 ass 为底，字幕的样式重命名(加 S_ 前缀)避免与背景撞名，字幕事件抬到高层(Layer 10)显示在上层。
    注：两个 ass 应基于同一视频分辨率（PlayResX/Y 一致），否则定位会有偏差。"""
    def read_lines(p):
        with open(p, encoding='utf-8-sig') as f:
            return f.read().splitlines()

    sub_lines = read_lines(editable_path)
    style_fmt = event_fmt = None
    sub_styles, sub_events = [], []
    for raw in sub_lines:
        l = raw.strip()
        if l.startswith('Format:'):
            fmt = [c.strip().lower() for c in l.split(':', 1)[1].split(',')]
            if 'text' in fmt:
                event_fmt = fmt
            else:
                style_fmt = fmt
        elif l.startswith('Style:') and style_fmt:
            vals = [v.strip() for v in l.split(':', 1)[1].split(',')]
            ni = style_fmt.index('name') if 'name' in style_fmt else 0
            vals[ni] = 'S_' + vals[ni]
            sub_styles.append('Style: ' + ','.join(vals))
        elif l.startswith('Dialogue:') and event_fmt:
            vals = l.split(':', 1)[1].split(',', len(event_fmt) - 1)
            if 'style' in event_fmt:
                si = event_fmt.index('style'); vals[si] = 'S_' + vals[si].strip()
            if 'layer' in event_fmt:
                vals[event_fmt.index('layer')] = '10'   # 抬高层级，叠在背景之上
            sub_events.append('Dialogue: ' + ','.join(vals))

    out, inserted = [], False
    for raw in read_lines(ref_path):
        if (not inserted) and raw.strip().startswith('[Events]'):
            out.extend(sub_styles); out.append(''); inserted = True
        out.append(raw)
    if not inserted:
        out.extend(sub_styles)
    out.extend(sub_events)
    with open(out_path, 'w', encoding='utf-8-sig') as f:
        f.write('\n'.join(out) + '\n')
    return out_path


def _extract_styles_events(path, prefix, layer):
    """从一个 ass 抽出 [V4+ Styles] 的 Style 行与 [Events] 的 Dialogue 行，
    样式名加 prefix 避免撞名，事件 Layer 改为 layer。"""
    def read_lines(p):
        with open(p, encoding='utf-8-sig') as f:
            return f.read().splitlines()
    style_fmt = event_fmt = None
    styles, events = [], []
    for raw in read_lines(path):
        l = raw.strip()
        if l.startswith('Format:'):
            fmt = [c.strip().lower() for c in l.split(':', 1)[1].split(',')]
            if 'text' in fmt:
                event_fmt = fmt
            else:
                style_fmt = fmt
        elif l.startswith('Style:') and style_fmt:
            vals = [v.strip() for v in l.split(':', 1)[1].split(',')]
            ni = style_fmt.index('name') if 'name' in style_fmt else 0
            vals[ni] = prefix + vals[ni]
            styles.append('Style: ' + ','.join(vals))
        elif l.startswith('Dialogue:') and event_fmt:
            vals = l.split(':', 1)[1].split(',', len(event_fmt) - 1)
            if 'style' in event_fmt:
                si = event_fmt.index('style'); vals[si] = prefix + vals[si].strip()
            if 'layer' in event_fmt:
                vals[event_fmt.index('layer')] = str(layer)
            events.append('Dialogue: ' + ','.join(vals))
    return styles, events


def merge_preview(out_path, base_path, overlay_paths):
    """以 base_path 为底，把 overlay_paths 列表里的 ass 依次叠在其上（样式改名、层级递增），
    合并成一个临时 ass，仅供 mpv 预览。各 ass 应基于同一视频分辨率。"""
    def read_lines(p):
        with open(p, encoding='utf-8-sig') as f:
            return f.read().splitlines()
    add_styles, add_events = [], []
    for i, ov in enumerate(overlay_paths):
        st, ev = _extract_styles_events(ov, f'O{i}_', 10 + i)
        add_styles += st; add_events += ev
    out, inserted = [], False
    for raw in read_lines(base_path):
        if (not inserted) and raw.strip().startswith('[Events]'):
            out.extend(add_styles); out.append(''); inserted = True
        out.append(raw)
    if not inserted:
        out.extend(add_styles)
    out.extend(add_events)
    with open(out_path, 'w', encoding='utf-8-sig') as f:
        f.write('\n'.join(out) + '\n')
    return out_path


def parse_txt(txt_path):
    """解析 run_asr 产出的 txt -> events 列表。"""
    events = []
    with open(txt_path, encoding='utf-8') as f:
        for line in f:
            m = _TXT_RE.match(line.strip())
            if m:
                events.append({
                    'start': _txt_hms_to_ms(m.group(1)),
                    'end':   _txt_hms_to_ms(m.group(2)),
                    'name':  m.group(3),
                    'text':  m.group(4),
                })
    return events


def gen_from_txt(style=None, wrap_chars=None):
    """txt -> ass，写到 ASS_PATH。
    style：样式覆盖（只给要改的键，其余取 DEFAULT_STYLE）；None=全用默认。
    wrap_chars：每行字数换行；None 时取 style 里的 wrap_chars，0/负=不换行。"""
    events = parse_txt(TXT_PATH)
    st = {**DEFAULT_STYLE, **(style or {})}
    n = int(wrap_chars if wrap_chars is not None else (st.get('wrap_chars', 0) or 0))
    if n > 0:
        for e in events:
            e['text'] = wrap_text(e['text'], n)
    play_w, play_h = video_size(VIDEO_PATH)
    write_ass(st, events, play_w, play_h)
    return events, play_w, play_h


# ── 渲染（ass + mp4 -> mp4）──────────────────────────────────────────────────

def unique_path(path):
    """若文件已存在，追加 (2)、(3)… 直到不冲突，避免覆盖。"""
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    i = 2
    while os.path.exists(f"{base}({i}){ext}"):
        i += 1
    return f"{base}({i}){ext}"


def subtitle_paths(video, overwrite):
    """ASR 用：返回 (txt, ass)，命名 视频名(字幕) 或不覆盖时 视频名(字幕1)/(字幕2)…
    txt 与 ass 用同一序号。"""
    stem = os.path.splitext(video)[0]
    def pair_exists(suf):
        return os.path.exists(f"{stem}(字幕{suf}).txt") or os.path.exists(f"{stem}(字幕{suf}).ass")
    if overwrite or not pair_exists(''):
        suf = ''
    else:
        i = 1
        while pair_exists(str(i)):
            i += 1
        suf = str(i)
    base = f"{stem}(字幕{suf})"
    return base + '.txt', base + '.ass'


def output_path(video, overwrite):
    """渲染输出：视频名(字幕).mp4 或不覆盖时 视频名(字幕1)/(字幕2)….mp4"""
    stem = os.path.splitext(video)[0]
    cand = f"{stem}(字幕).mp4"
    if overwrite or not os.path.exists(cand):
        return cand
    i = 1
    while os.path.exists(f"{stem}(字幕{i}).mp4"):
        i += 1
    return f"{stem}(字幕{i}).mp4"


def delete_file(path):
    """直接删除中间文件；不存在或删除失败则忽略（不影响主流程）。"""
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


_HAS_NVENC = None   # 缓存 h264_nvenc 是否可用（None=未检测）


def _has_nvenc():
    """检测当前 ffmpeg 是否支持 NVIDIA 硬件编码器 h264_nvenc（只检一次并缓存）。"""
    global _HAS_NVENC
    if _HAS_NVENC is None:
        try:
            out = subprocess.run(['ffmpeg', '-hide_banner', '-encoders'],
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, encoding='utf-8', errors='ignore').stdout
            _HAS_NVENC = 'h264_nvenc' in (out or '')
        except Exception:
            _HAS_NVENC = False
    return _HAS_NVENC


def burn(progress_cb=None, overwrite=None, on_proc=None, out_path=None):
    """渲染。progress_cb(percent) 可选；out_path 给定则直接用作输出（由调用方决定命名/覆盖），
    否则按 overwrite 走默认命名。on_proc(Popen) 可选：拿子进程句柄以便中止；中止/失败删不完整输出。
    视频编码优先用 GPU 硬件编码器 h264_nvenc（+硬件解码），对齐主管线，CPU 占用低、快；
    无 N 卡/不支持时回退到 libx264 veryfast（仍比默认 medium 快、CPU 占用低）。"""
    if overwrite is None:
        overwrite = OVERWRITE_OUTPUT
    ass_dir = os.path.dirname(ASS_PATH)
    ass_file = os.path.basename(ASS_PATH)
    output = out_path or output_path(VIDEO_PATH, overwrite)
    total = video_duration_ms(VIDEO_PATH) or 1
    if _has_nvenc():
        in_opts = ['-hwaccel', 'auto']                       # 硬件解码
        venc = ['-c:v', 'h264_nvenc', '-b:v', '15M']          # GPU 硬件编码（对齐主管线）
    else:
        in_opts = []
        venc = ['-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20']   # 回退：CPU 但提速
    # cwd 切到 ass 目录、filter 只传文件名，避免 Windows 路径冒号转义问题
    cmd = ['ffmpeg', '-y', *in_opts, '-i', VIDEO_PATH,
           '-vf', f"ass={ass_file}",
           *venc, '-c:a', 'copy',
           '-progress', 'pipe:1', '-nostats', output]
    p = subprocess.Popen(cmd, cwd=ass_dir, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='ignore')
    if on_proc:
        on_proc(p)
    for line in p.stdout:
        line = line.strip()
        if line.startswith('out_time_ms=') and progress_cb:
            try:
                done = int(line.split('=')[1]) // 1000   # 微秒->毫秒
                progress_cb(min(99, int(done * 100 / total)))
            except ValueError:
                pass
    p.wait()
    if p.returncode != 0:
        delete_file(output)   # 失败/被中止：删掉不完整输出
        raise RuntimeError(f"ffmpeg 失败，退出码 {p.returncode}")
    if progress_cb:
        progress_cb(100)
    return output


# ── 视频剪辑（无损快切 / 拼接）────────────────────────────────────────────────

def cut_clip(video, start_ms, end_ms, out_path, progress_cb=None, on_proc=None):
    """无损快切：从 video 取 [start_ms, end_ms) 一段，-c copy 写到 out_path。
    -ss 作为 input 选项快速 seek（吸附最近关键帧），-t 指定时长避免 -ss/-to 语义歧义。
    progress_cb(percent)/on_proc(Popen) 可选。返回 out_path；失败/被中止抛 RuntimeError 并删半成品。"""
    start = max(0, int(start_ms)) / 1000
    dur = max(0, int(end_ms) - int(start_ms)) / 1000
    total = max(1, int(end_ms) - int(start_ms))
    cmd = ['ffmpeg', '-y', '-ss', f'{start:.3f}', '-i', video, '-t', f'{dur:.3f}',
           '-c', 'copy', '-avoid_negative_ts', 'make_zero',
           '-progress', 'pipe:1', '-nostats', out_path]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding='utf-8', errors='ignore')
    if on_proc:
        on_proc(p)
    for line in p.stdout:
        line = line.strip()
        if line.startswith('out_time_ms=') and progress_cb:
            try:
                done = int(line.split('=')[1]) // 1000   # 微秒->毫秒
                progress_cb(min(99, int(done * 100 / total)))
            except ValueError:
                pass
    p.wait()
    if p.returncode != 0:
        delete_file(out_path)
        raise RuntimeError(f"ffmpeg 切割失败，退出码 {p.returncode}")
    if progress_cb:
        progress_cb(100)
    return out_path


def concat_clips(segment_paths, out_path):
    """把已切好的多个片段按顺序无损拼接成一个文件（concat demuxer，-c copy）。
    片段应同源同编码。返回 out_path；失败抛 RuntimeError。"""
    import tempfile
    fd, listfile = tempfile.mkstemp(suffix='.txt', prefix='dmr_concat_', dir=temp_dir())
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            for sp in segment_paths:
                # 用正斜杠避免反斜杠转义问题；单引号按 concat 规则转义
                ap = os.path.abspath(sp).replace('\\', '/').replace("'", "'\\''")
                f.write(f"file '{ap}'\n")
        cmd = ['ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', listfile,
               '-c', 'copy', out_path]
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True, encoding='utf-8', errors='ignore')
        if p.returncode != 0:
            tail = (p.stdout or '')[-2000:]
            raise RuntimeError(f"ffmpeg 拼接失败，退出码 {p.returncode}\n{tail}")
    finally:
        delete_file(listfile)
    return out_path


def clip_output_path(video, index=None, ext=None):
    """剪辑输出命名（过 unique_path 不覆盖源）：
    index 给定 -> 视频名_片段{index}{ext}（各导出一个）；否则 视频名_剪辑{ext}（拼接）。
    ext 默认沿用源视频扩展名。"""
    stem, vext = os.path.splitext(video)
    ext = ext or vext
    cand = f"{stem}_片段{index}{ext}" if index is not None else f"{stem}_剪辑{ext}"
    return unique_path(cand)


# ── 音频波形（振幅包络）────────────────────────────────────────────────────────

def waveform_peaks(video, peaks_per_sec=50, max_buckets=120000, sample_rate=4000):
    """解码音频为单声道低采样 PCM，按 peaks_per_sec 个/秒的密度分桶取峰值（桶数随时长走，
    便于放大后仍有细节；上限 max_buckets），归一化到 0~1（自动增益，安静音频也可见）。
    返回 list[float]；无音频/失败返回 []。"""
    cmd = ['ffmpeg', '-v', 'quiet', '-i', video, '-vn', '-ac', '1',
           '-ar', str(sample_rate), '-f', 's16le', '-']
    try:
        raw = subprocess.run(cmd, stdout=subprocess.PIPE).stdout
    except Exception:
        return []
    if not raw:
        return []
    try:
        import numpy as np
        data = np.frombuffer(raw, dtype='<i2')
        n = data.size
        if n == 0:
            return []
        buckets = max(1, min(max_buckets, int(n / sample_rate * peaks_per_sec)))
        absd = np.abs(data.astype(np.int32))
        starts = np.linspace(0, n, buckets, endpoint=False).astype(int)
        peaks = np.maximum.reduceat(absd, starts).astype(float)   # 向量化分桶取峰
        mx = float(peaks.max()) or 1.0
        return (peaks / mx).tolist()
    except Exception:
        import array
        data = array.array('h')
        data.frombytes(raw[:len(raw) // 2 * 2])
        n = len(data)
        if n == 0:
            return []
        buckets = max(1, min(max_buckets, int(n / sample_rate * peaks_per_sec)))
        peaks, per = [], n / buckets
        for b in range(buckets):
            lo = int(b * per); hi = int((b + 1) * per)
            if hi <= lo:
                hi = lo + 1
            seg = data[lo:hi]
            peaks.append(max(max(seg), -min(seg)) if seg else 0)
        mx = max(peaks) or 1
        return [p / mx for p in peaks]


def get_waveform(video, peaks_per_sec=50):
    """带缓存的波形：按 视频路径+mtime+密度 缓存到临时目录的 json，命中则直接读。"""
    import json, hashlib
    try:
        key = f"{os.path.abspath(video)}|{os.path.getmtime(video)}|pps{peaks_per_sec}"
    except OSError:
        return waveform_peaks(video, peaks_per_sec)
    h = hashlib.md5(key.encode('utf-8')).hexdigest()[:16]
    cache = os.path.join(temp_dir(), f"dmr_wave_{h}.json")
    if os.path.exists(cache):
        try:
            with open(cache, encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    peaks = waveform_peaks(video, peaks_per_sec)
    if peaks:
        try:
            with open(cache, 'w', encoding='utf-8') as f:
                json.dump(peaks, f)
        except Exception:
            pass
    return peaks


# ════════════════════════════════════════════════════════════════════════════
#  统一接口：视频 -> (可选)ASR -> 带样式、按字数换行的字幕 ass
#  CLI / DMR / GUI 都走 generate_subtitle_ass
# ════════════════════════════════════════════════════════════════════════════

def write_srt(events, path):
    """把 events([{start,end,text}]) 写成 SubRip .srt（时间用逗号毫秒，可导入剪映/Pr 等）。"""
    def ts(ms):
        ms = max(0, int(ms))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"
    blocks = []
    for i, e in enumerate(events, 1):
        text = (e.get('text') or '').replace('\\N', '\n').replace('\\n', '\n')
        blocks.append(f"{i}\n{ts(e['start'])} --> {ts(e['end'])}\n{text}\n")
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(blocks))
    return path


def generate_subtitle_ass(video, style=None, out_ass=None, do_asr=True,
                          out_txt=None, show_output=False, trash_txt=False,
                          wrap_chars=None, on_proc=None, out_srt=None, make_ass=True):
    """
    对 video 做（可选）语音识别并产出字幕 ass，返回 ass 的绝对路径。
    同步设置全局 VIDEO_PATH/TXT_PATH/ASS_PATH，供 GUI 预览/载入复用。

    参数：
      video       视频路径
      style       样式覆盖 dict（只需给要改的键，其余取 DEFAULT_STYLE）
      out_ass     输出 ass 名/路径；默认 同目录下 视频名(字幕).ass；相对名则放视频目录
      do_asr      True=强制重新识别；False=已有 txt 则直接用，不重跑 ASR
      out_txt     ASR 输出 txt 路径；默认 视频名.txt（set_video 推导）
      show_output ASR 进度是否打印到命令行（False=吞输出保持干净）
      trash_txt   生成 ass 后是否删除中间 txt
      wrap_chars  每行字数换行；None=取 style 的 wrap_chars，<=0 不换行
    """
    global TXT_PATH, ASS_PATH
    set_video(video)                      # 设 VIDEO_PATH/TXT_PATH/ASS_PATH 为视频同名默认
    st = {**DEFAULT_STYLE, **(style or {})}

    if out_txt is not None:
        TXT_PATH = out_txt                # 覆盖默认 txt 命名（GUI 用 视频名(字幕)[N].txt）
    if out_ass is None:
        out_ass = os.path.splitext(video)[0] + '(字幕).ass'
    elif not os.path.isabs(out_ass):
        out_ass = os.path.join(os.path.dirname(video), out_ass)
    ASS_PATH = out_ass

    if do_asr or not os.path.exists(TXT_PATH):
        task_state['msg'] = '正在语音识别…'
        run_asr_subprocess(show_output=show_output, on_proc=on_proc)

    task_state['msg'] = '正在生成 ass'
    events = parse_txt(TXT_PATH)
    if out_srt:                       # 先按原始文本（未换行）导出 srt，供剪映等导入
        write_srt(events, out_srt)
    n = int(wrap_chars if wrap_chars is not None else (st.get('wrap_chars', 0) or 0))
    if n > 0:
        for e in events:
            e['text'] = wrap_text(e['text'], n)

    if make_ass:
        w, h = video_size(video)
        write_ass(st, events, w, h, path=out_ass)
    if trash_txt:
        delete_file(TXT_PATH)
    return out_ass


def _cli():
    import argparse
    ap = argparse.ArgumentParser(description='视频 -> 语音识别 -> 字幕 ass，打印 ass 路径')
    ap.add_argument('video', help='视频路径')
    ap.add_argument('--font', help='字体名')
    ap.add_argument('--size', type=int, help='字号(像素)')
    ap.add_argument('--margin', type=int, help='下边距(像素)')
    ap.add_argument('--font-color', help='字色 #RRGGBB')
    ap.add_argument('--outline', type=float, help='描边宽度')
    ap.add_argument('--outline-color', help='描边色 #RRGGBB')
    ap.add_argument('--wrap', type=int, help='每行最多字数(0=不换行)')
    ap.add_argument('--out', help='输出 ass 名/路径，默认 视频名(字幕).ass')
    ap.add_argument('--no-asr', action='store_true', help='已有同名 txt 则不重跑识别')
    a = ap.parse_args()

    style = {}
    if a.font is not None:          style['font_name'] = a.font
    if a.size is not None:          style['font_size'] = a.size
    if a.margin is not None:        style['margin_bottom'] = a.margin
    if a.font_color is not None:    style['font_color'] = a.font_color
    if a.outline is not None:       style['outline'] = a.outline
    if a.outline_color is not None: style['outline_color'] = a.outline_color
    if a.wrap is not None:          style['wrap_chars'] = a.wrap

    out = generate_subtitle_ass(a.video, style=style, out_ass=a.out, do_asr=not a.no_asr)
    print(out)   # 最后一行打印 ass 路径，供调用方捕获
    return out


if __name__ == '__main__':
    _cli()
