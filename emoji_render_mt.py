# -*- coding: utf-8 -*-
"""
弹幕彩色 emoji 渲染（多核并行版，方案2）

用法:
    python emoji_render_mt.py <输入.flv> <输入.ass> <输出.mp4>
        [--workers 4] [--segmul 3] [--vb 8M] [--ab 160K] [--no-hwaccel]
        [--emoji-font 路径] [--emoji-pack 表情目录] [--resize 1280x720|0.5]

特性:
    - 无损切段 → 每段独立进程并行渲染(libass烧普通弹幕 + nv12上贴彩色emoji) → concat → 混原音。
    - 文字字体从 ASS 的 R2L 样式读取(Fontname+Bold,经 matplotlib 解析为文件),与普通弹幕一致。
    - emoji 字体可换: --emoji-font 指定 ttf/名称(默认系统 Segoe UI Emoji,矢量、最快最清晰;
      位图字体如 Noto 也支持,会按 strike 缩放,稍慢略糊)。
    - [xxx] 方括号表情替换: --emoji-pack 指向目录,把弹幕里的 [笑] 换成 目录/笑.png。
    - --resize: 输出缩放,"宽x高" 或缩放系数(如 0.5);合成在原分辨率完成,仅最终编码缩放。
"""
import os, re, sys, json, time, shutil, subprocess, threading, queue, glob
import numpy as np
from PIL import Image, ImageDraw, ImageFont

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"

def _default_emoji_font():
    """系统自带彩色 emoji 字体(跨平台兜底)"""
    if os.name == "nt":
        return r"C:\Windows\Fonts\seguiemj.ttf"
    for p in ("/System/Library/Fonts/Apple Color Emoji.ttc",
              "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
              "/usr/share/fonts/noto-cjk/NotoColorEmoji.ttf",
              "/usr/share/fonts/google-noto-emoji/NotoColorEmoji.ttf"):
        if os.path.isfile(p):
            return p
    return "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"

DEFAULT_EMOJI_FONT = _default_emoji_font()
SCALE = 0.735
OUT = 1
QDEPTH = 8

# 无字形整形(shaping)时,这些"组合/修饰"码点会渲成多余方块,渲染前一律去掉:
#   FE00-FE0F 变体选择符、200D 零宽连接(ZWJ)、20E3 keycap 封闭符、1F3FB-1F3FF 肤色修饰符
_STRIP = {0x200D: None, 0x20E3: None}
_STRIP.update({c: None for c in range(0xFE00, 0xFE10)})     # 变体选择符
_STRIP.update({c: None for c in range(0x1F3FB, 0x1F400)})   # 肤色修饰符


def is_emoji(c):
    o = ord(c)
    return (0x1F300 <= o <= 0x1FAFF) or (0x1F000 <= o <= 0x1F0FF) \
        or (0x2600 <= o <= 0x27BF) or (0x2300 <= o <= 0x23FF) or (0x2B00 <= o <= 0x2BFF) \
        or (0x1F1E6 <= o <= 0x1F1FF) \
        or (0xFE00 <= o <= 0xFE0F) or (0x1F3FB <= o <= 0x1F3FF) or o == 0x200D or o == 0x20E3
def ass_time(s):
    h, m, rest = s.split(":"); return int(h)*3600 + int(m)*60 + float(rest)
def sec_to_ass(t):
    if t < 0: t = 0.0
    h = int(t // 3600); m = int((t % 3600) // 60); s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"
def fmt(sec):
    sec = int(sec); return f"{sec//60}分{sec%60:02d}秒"

SHORTCODE_RE = re.compile(r"\[([^\[\]]{1,20})\]")

def has_special(body, pack):
    """该弹幕是否需要走精灵路径(含unicode emoji 或 已知方括号表情)"""
    clean = re.sub(r"\{[^}]*\}", "", body)
    if any(is_emoji(c) for c in clean):
        return True
    if pack:
        for m in SHORTCODE_RE.finditer(clean):
            if m.group(1) in pack:
                return True
    return False


def tokenize(text, pack):
    """切成 [(kind, payload)]，kind: 'text' / 'emoji' / 'png'(payload=png路径)"""
    tokens = []
    parts = SHORTCODE_RE.split(text)   # 偶数位是普通文本,奇数位是 [] 里的内容
    for i, part in enumerate(parts):
        if i % 2 == 1:  # 方括号内容
            if pack and part in pack:
                tokens.append(("png", pack[part])); continue
            part = f"[{part}]"  # 不在表情包里 → 当普通文字
        cur = ""; ce = None
        for ch in part:
            e = is_emoji(ch)
            if cur and e != ce:
                tokens.append(("emoji" if ce else "text", cur)); cur = ""
            cur += ch; ce = e
        if cur:
            tokens.append(("emoji" if ce else "text", cur))
    return tokens


def parse_pill(name):
    """ASS Name 字段的药丸标记 'pill#RRGGBB#pad#border' -> dict 或 None。"""
    if not name or not name.startswith("pill"):
        return None
    p = name.split("#")
    col = p[1] if len(p) > 1 and len(p[1]) == 6 else "FFC0CB"
    try: pad = int(p[2]) if len(p) > 2 else 6
    except ValueError: pad = 6
    try: bor = int(p[3]) if len(p) > 3 else 3
    except ValueError: bor = 3
    return dict(color=[int(col[0:2], 16), int(col[2:4], 16), int(col[4:6], 16)], pad=pad, border=max(1, bor))


# ============================ 单段渲染（worker） ============================
def render_segment(cfg):
    seg_file = cfg["seg"]; textonly_ass = cfg["textonly"]; out_ts = cfg["out"]; prog_file = cfg["prog"]
    W, H, fps, vb = cfg["W"], cfg["H"], cfg["fps"], cfg["vb"]
    use_hwaccel = cfg["hwaccel"]; emoji_events = cfg["events"]
    pack = cfg.get("pack", {})
    segdir = os.path.dirname(os.path.abspath(out_ts)) or "."

    fs_ass = cfg["fs"]                # ASS 字号(屏幕像素);药丸高按它算,与 libass 药丸一致
    corr = max(1, int(round(fs_ass * SCALE)))
    tf = ImageFont.truetype(cfg["textfont"], corr)
    # emoji 字体:矢量(COLR,如 Segoe)可任意尺寸直接用;位图(CBDT,如 Noto)只有固定 strike,需缩放
    try:
        ef = ImageFont.truetype(cfg["emojifont"], corr); ef_scale = 1.0
    except OSError:
        ef = None
        for _sz in (109, 128, 136, 96, 160, 64, 32):
            try: ef = ImageFont.truetype(cfg["emojifont"], _sz); break
            except OSError: continue
        if ef is None:
            # 既不是矢量也读不到任何 strike(文件损坏/不是字体)→ 回退系统 emoji 字体,再不行用文字字体,绝不崩
            for fb in (DEFAULT_EMOJI_FONT, cfg["textfont"]):
                try: ef = ImageFont.truetype(fb, corr); break
                except OSError: continue
            if ef is None: ef = ImageFont.load_default()
        ef_scale = corr / ef.size if getattr(ef, "size", 0) else 1.0
    e_asc, e_desc = ef.getmetrics()
    asc, desc = tf.getmetrics()
    png_h = int(round(corr * 1.25))   # 方括号表情显示高度(可调,不影响"不裁切")
    png_center = -int(round(asc * 0.38))  # 表情垂直中心相对基线(负=上),约文字主体中心

    png_cache = {}
    def load_png(path):
        """按高度等比缩放,保持宽高比,不裁切;保留透明通道"""
        if path in png_cache: return png_cache[path]
        im = Image.open(path).convert("RGBA")
        w = max(1, int(round(im.width * png_h / im.height)))
        im = im.resize((w, png_h), Image.LANCZOS)
        png_cache[path] = im
        return im

    cache = {}
    def get_sprite(text, rgb, pill=None):
        pkey = (tuple(pill["color"]), pill["pad"], pill["border"]) if pill else None
        key = (text, tuple(rgb), pkey)
        if key in cache: return cache[key]
        toks = tokenize(text, pack)
        has_png = any(k == "png" for k, _ in toks)
        # 内容宽
        content_w = 0
        for kind, pl in toks:
            if kind == "png":
                content_w += load_png(pl).width
            elif kind == "emoji":
                content_w += int(ef.getlength(pl) * ef_scale)
            else:
                content_w += int(tf.getlength(pl))
        # 内容相对基线的上下范围:文字 [-asc,+desc];表情居中于 png_center
        ptop = png_center - png_h // 2; pbot = png_center + (png_h - png_h // 2)
        top_txt = max(asc, (-ptop if has_png else 0))
        bot_txt = max(desc, (pbot if has_png else 0))
        if pill:
            # 药丸高 = ASS字号 + 2*buff(与 libass 药丸完全一致),竖直居中于文字行;
            # 左右半圆留白 pad_x = 药丸半高 = (fs+2pv)/2(与 asswriter 的 pad_x 一致)。
            pv = pill["pad"]; pb = pill["border"]
            pad_x = (fs_ass + 2 * pv) / 2.0
            # 文字底在精灵内位于 baseline+desc;药丸需上达 fs+pv-desc、下达 desc+pv(相对基线)
            top_ext = int(round(max(top_txt, fs_ass + pv - desc) + pb + OUT))
            bot_ext = int(round(max(bot_txt, desc + pv) + pb + OUT))
            content_x = int(round(pad_x + pb + OUT))
            total_w = content_w + 2 * content_x
        else:
            top_ext = top_txt + OUT
            bot_ext = bot_txt + OUT
            content_x = OUT
            total_w = content_w + 2 * OUT
        baseline = top_ext
        Hs = top_ext + bot_ext
        spr = Image.new("RGBA", (max(int(total_w), 1), int(Hs)), (0, 0, 0, 0))
        d = ImageDraw.Draw(spr); x = content_x
        for kind, pl in toks:
            if kind == "png":
                im = load_png(pl)
                spr.alpha_composite(im, (int(x), baseline + ptop))   # 完整贴入,绝不越界裁切
                x += im.width
            elif kind == "emoji":
                if ef_scale == 1.0:
                    d.text((x, baseline), pl, font=ef, embedded_color=True, anchor="ls")
                    x += int(ef.getlength(pl))
                else:   # 位图字体:按原生 strike 渲染后缩放贴入
                    ew = max(1, int(ef.getlength(pl)))
                    tmp = Image.new("RGBA", (ew, e_asc + e_desc), (0, 0, 0, 0))
                    ImageDraw.Draw(tmp).text((0, e_asc), pl, font=ef, embedded_color=True, anchor="ls")
                    tmp = tmp.resize((max(1, int(ew * ef_scale)), max(1, int((e_asc + e_desc) * ef_scale))), Image.LANCZOS)
                    spr.alpha_composite(tmp, (int(x), baseline - int(round(e_asc * ef_scale))))
                    x += tmp.width
            else:
                d.text((x, baseline), pl, font=tf, fill=tuple(rgb) + (255,),
                       stroke_width=OUT, stroke_fill=(0, 0, 0, 255), anchor="ls")
                x += int(tf.getlength(pl))
        tbo = baseline + desc   # 文字底(\an1锚点)距精灵顶的偏移,定位用
        if pill:
            # 药丸描边(只描边,中间透明):高=fs+2pv,竖直居中于文字行(与 libass 药丸位置一致)
            pb = pill["border"]; col = tuple(pill["color"]) + (255,)
            x0p = OUT + pb / 2.0; x1p = total_w - OUT - pb / 2.0
            y0p = tbo - fs_ass - pv          # 药丸顶 = 文字底 - 字高 - buff
            y1p = tbo + pv                   # 药丸底 = 文字底 + buff
            r = (y1p - y0p) / 2.0            # = (fs+2pv)/2,两端半圆
            d.rounded_rectangle([x0p, y0p, x1p, y1p], radius=r, outline=col, width=pb)
        a = np.asarray(spr).astype(np.float32)
        R, G, B = a[:, :, 0], a[:, :, 1], a[:, :, 2]; A = a[:, :, 3] / 255.0
        sY = 16 + 0.1826 * R + 0.6142 * G + 0.0620 * B
        sU = 128 - 0.1006 * R - 0.3386 * G + 0.4392 * B
        sV = 128 + 0.4392 * R - 0.3989 * G - 0.0403 * B
        lbo = content_x         # 内容左缘距精灵左缘的偏移,使内容对齐到 \move 的 x
        cache[key] = (sY, sU, sV, A, spr.width, spr.height, tbo, lbo)
        return cache[key]
    for ev in emoji_events:
        ev["_spr"] = get_sprite(ev["text"], ev["rgb"], ev.get("pill"))

    dec_cmd = [FFMPEG, "-v", "error", "-fflags", "+genpts+discardcorrupt"]
    if use_hwaccel: dec_cmd += ["-hwaccel", "auto"]
    # 字幕（可选）：和弹幕在同一遍 libass 里一起烧，几乎不增加编码成本
    sub_seg = cfg.get("subtitle")
    sub_filter = f",subtitles={os.path.basename(sub_seg)}" if sub_seg else ""
    dec_cmd += ["-i", os.path.basename(seg_file),
                # fps 在 subtitles 之前：先把(可变帧率的)源铺成均匀 CFR，再让 libass 逐帧画弹幕 → 顺滑
                "-vf", f"fps={fps},subtitles={os.path.basename(textonly_ass)}{sub_filter}",
                "-fps_mode", "cfr", "-f", "rawvideo", "-pix_fmt", "nv12", "-"]
    dec = subprocess.Popen(dec_cmd, cwd=segdir, stdout=subprocess.PIPE)
    venc = cfg.get("venc", "h264_nvenc")
    # 缩放放在编码段(合成在原分辨率完成,emoji \move 坐标才不会错位)
    scale_args = []
    rs = cfg.get("resize")
    if rs and rs != f"{W}x{H}":
        rw, rh = rs.split("x"); scale_args = ["-vf", f"scale={rw}:{rh}"]
    enc = subprocess.Popen(
        [FFMPEG, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "nv12",
         "-s", f"{W}x{H}", "-r", f"{fps}", "-i", "-", "-an", *scale_args,
         "-c:v", venc, "-b:v", vb, "-f", "mpegts", os.path.basename(out_ts)],
        cwd=segdir, stdin=subprocess.PIPE)

    fb = W * H * 3 // 2
    read_q = queue.Queue(maxsize=QDEPTH); write_q = queue.Queue(maxsize=QDEPTH)

    def readexactly(rd, n):
        chunks = []; got = 0
        while got < n:
            ch = rd.read(n - got)
            if not ch: return None
            chunks.append(ch); got += len(ch)
        return chunks[0] if len(chunks) == 1 else b"".join(chunks)
    def reader():
        i = 0
        while True:
            buf = readexactly(dec.stdout, fb)
            if buf is None: break
            read_q.put((i, buf)); i += 1
        read_q.put(None)
    def writer():
        while True:
            item = write_q.get()
            if item is None: break
            try: enc.stdin.write(item)
            except (BrokenPipeError, OSError): break
    rt = threading.Thread(target=reader, daemon=True); wt = threading.Thread(target=writer, daemon=True)
    rt.start(); wt.start()

    n = 0; last = 0.0
    while True:
        item = read_q.get()
        if item is None: break
        i, buf = item
        t = i / fps
        active = [ev for ev in emoji_events if ev["s"] <= t <= ev["e"]]
        if active:
            arr = np.frombuffer(buf, np.uint8).copy()
            Y = arr[:W * H].reshape(H, W)
            UV = arr[W * H:].reshape(H // 2, W // 2, 2)
            for ev in active:
                sY, sU, sV, A, w, h, tbo, lbo = ev["_spr"]
                p = (t - ev["s"]) / (ev["e"] - ev["s"])
                xa = int(round(ev["x0"] + (ev["x1"] - ev["x0"]) * p)) - lbo   # 内容左缘对齐到 \move 的 x
                ya = int(round(ev["y0"]) - tbo)   # 文字底对齐到 \move 的 y,精灵高度变化不影响文字位置
                sx0 = max(0, -xa); sy0 = max(0, -ya); dx0 = max(0, xa); dy0 = max(0, ya)
                cw = min(w - sx0, W - dx0); ch = min(h - sy0, H - dy0)
                if cw <= 0 or ch <= 0: continue
                aR = A[sy0:sy0 + ch, sx0:sx0 + cw] * ev["op"]
                Yreg = Y[dy0:dy0 + ch, dx0:dx0 + cw].astype(np.float32)
                Y[dy0:dy0 + ch, dx0:dx0 + cw] = (sY[sy0:sy0 + ch, sx0:sx0 + cw] * aR + Yreg * (1 - aR)).astype(np.uint8)
                uy, ux = dy0 // 2, dx0 // 2
                aSub = aR[::2, ::2]; sUs = sU[sy0:sy0 + ch:2, sx0:sx0 + cw:2]; sVs = sV[sy0:sy0 + ch:2, sx0:sx0 + cw:2]
                Ud = UV[uy:uy + aSub.shape[0], ux:ux + aSub.shape[1], 0]
                Vd = UV[uy:uy + aSub.shape[0], ux:ux + aSub.shape[1], 1]
                hh = min(aSub.shape[0], sUs.shape[0], Ud.shape[0]); ww = min(aSub.shape[1], sUs.shape[1], Ud.shape[1])
                if hh <= 0 or ww <= 0: continue
                a2 = aSub[:hh, :ww]
                Ud[:hh, :ww] = (sUs[:hh, :ww] * a2 + Ud[:hh, :ww].astype(np.float32) * (1 - a2)).astype(np.uint8)
                Vd[:hh, :ww] = (sVs[:hh, :ww] * a2 + Vd[:hh, :ww].astype(np.float32) * (1 - a2)).astype(np.uint8)
            write_q.put(arr.tobytes())
        else:
            write_q.put(buf)
        n += 1
        now = time.time()
        if now - last >= 0.4:
            last = now; open(prog_file, "w").write(str(n))
    write_q.put(None)
    rt.join(timeout=10); wt.join(timeout=10)
    try: dec.stdout.close()
    except Exception: pass
    try: enc.stdin.close()
    except Exception: pass
    enc.wait(); dec.wait()
    if n == 0:   # 一帧都没解出来(段文件不可读/解码失败)→ 判失败
        open(prog_file, "w").write("ERR")
        raise RuntimeError(f"该段解码 0 帧(seg 不可读?): {os.path.basename(seg_file)}")
    open(prog_file, "w").write(f"{n} done")
    return n


# ============================ 主流程 ============================
def resolve_font(family, bold):
    """字体名 -> 文件路径(用 matplotlib)"""
    try:
        import matplotlib.font_manager as fm
        return fm.findfont(fm.FontProperties(family=family, weight="bold" if bold else "normal"),
                           fallback_to_default=True)
    except Exception:
        return r"C:\Windows\Fonts\msyhbd.ttc" if bold else r"C:\Windows\Fonts\msyh.ttc"


def resolve_emoji_font(val):
    """--emoji-font 的值 -> 可用的字体文件路径。
    支持:绝对/相对路径、省略扩展名、裸字体名(在常见目录和系统字体里找)、matplotlib 按字体族查找。"""
    if not val:
        return DEFAULT_EMOJI_FONT
    here = os.path.dirname(os.path.abspath(__file__))
    exts = ["", ".ttf", ".ttc", ".otf"]
    # 1) 直接当路径(含省略扩展名);2) 在 脚本目录 / 脚本/test / 当前目录 / Windows字体 里找
    dirs = ["", os.getcwd(), here, os.path.join(here, "test"), r"C:\Windows\Fonts"]
    for d in dirs:
        for ext in exts:
            cand = os.path.join(d, val + ext) if d else (val + ext)
            if os.path.isfile(cand):
                return os.path.abspath(cand)
    # 3) 当作字体族名,用 matplotlib 查
    try:
        import matplotlib.font_manager as fm
        p = fm.findfont(fm.FontProperties(family=val), fallback_to_default=False)
        if p and os.path.isfile(p):
            return os.path.abspath(p)
    except Exception:
        pass
    print(f"[!] 找不到 emoji 字体 '{val}',回退到系统 Segoe UI Emoji。"
          f"(可改用完整路径,如 test\\NotoColorEmoji.ttf)")
    return DEFAULT_EMOJI_FONT


def check_tool(name):
    """工具是否可用(在 PATH 或当前目录)"""
    try:
        subprocess.run([name, "-version"], capture_output=True)
        return True
    except (FileNotFoundError, OSError):
        return False


def audio_codec(path):
    """探测第一条音轨编码名(用于决定能否 -c:a copy);失败返回空串"""
    try:
        r = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "a:0",
                            "-show_entries", "stream=codec_name", "-of", "csv=p=0", path],
                           capture_output=True, text=True)
        return r.stdout.strip()
    except Exception:
        return ""


def load_pack(pack_dirs):
    """目录里的 png -> {名字: 路径}。支持逗号分隔多个目录,靠后的覆盖靠前的(房间专属覆盖通用)。"""
    pack = {}
    if not pack_dirs:
        return pack
    for d in str(pack_dirs).split(","):
        d = d.strip()
        if not d or not os.path.isdir(d):
            continue
        for fn in os.listdir(d):
            name, ext = os.path.splitext(fn)
            if ext.lower() in (".png", ".webp", ".gif"):
                pack[name] = os.path.abspath(os.path.join(d, fn))
    return pack


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--worker" in sys.argv:
        cfgpath = sys.argv[sys.argv.index("--worker") + 1]
        try:
            with open(cfgpath, encoding="utf-8") as f:
                cfg = json.load(f)
            render_segment(cfg)
        except Exception:
            import traceback; traceback.print_exc()
            try:
                with open(cfgpath, encoding="utf-8") as f:
                    pf = json.load(f).get("prog")
                if pf: open(pf, "w").write("ERR")
            except Exception:
                pass
            sys.exit(1)
        return

    if len(args) < 3:
        print(__doc__); sys.exit(1)
    flv, ass, out_mp4 = os.path.abspath(args[0]), os.path.abspath(args[1]), os.path.abspath(args[2])
    def opt(name, default=None):
        return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default
    for t in (FFMPEG, FFPROBE):
        if not check_tool(t):
            print(f"找不到 {t},请确认已安装并加入 PATH(或放到脚本目录)。"); sys.exit(1)
    workers = int(opt("--workers", 4)); segmul = int(opt("--segmul", 3))
    vb = opt("--vb", "8M"); ab = opt("--ab", "160K")
    vencoder = opt("--vencoder", "h264_nvenc")
    use_hwaccel = "--no-hwaccel" not in sys.argv
    resize = opt("--resize")   # "宽x高" 或 缩放系数(如 0.5);留空=原分辨率
    subtitle_ass = opt("--subtitle")   # ASR 字幕 ass（可选）；和弹幕一起在同一遍 libass 烧
    emoji_font = resolve_emoji_font(opt("--emoji-font"))
    try:   # 位图字体(如 Noto)按 strike 缩放,较慢且略糊;矢量(Segoe)更快更清晰
        ImageFont.truetype(emoji_font, 32)
    except OSError:
        print(f"[提示] {os.path.basename(emoji_font)} 是位图字体(按 strike 缩放,较慢/略糊);"
              f"追求速度与清晰可用矢量 Segoe UI Emoji(默认)。")
    pack = load_pack(opt("--emoji-pack"))
    for pth in (flv, ass):
        if not os.path.exists(pth):
            print(f"找不到文件: {pth}"); sys.exit(1)

    work = os.path.join(os.path.dirname(out_mp4) or ".", "_mt_" + os.path.splitext(os.path.basename(out_mp4))[0])
    if os.path.exists(work): shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work, exist_ok=True)

    import av
    c = av.open(flv); v = c.streams.video[0]
    W, H = v.codec_context.width, v.codec_context.height
    # 输出帧率：--fps 优先；否则取源的标称帧率(guessed_rate，干净的 60)而非 average_rate
    # (直播是可变帧率，average 会算成 62 这种)。配合解码滤镜里 fps 放在 subtitles 之前，
    # 让 libass 在均匀的 CFR 时间轴上逐帧画弹幕 → 滚动弹幕顺滑，且几乎不增加耗时。
    if opt("--fps"):
        fps = float(opt("--fps"))
    else:
        try:
            fps = float(v.guessed_rate or v.average_rate)
        except Exception:
            fps = float(v.average_rate)
    dur = float(c.duration) / 1e6 if c.duration else 0
    c.close()
    if not dur:
        def _probe(entries, sel=None):
            cmd = [FFPROBE, "-v", "error"]
            if sel: cmd += ["-select_streams", sel]
            cmd += ["-show_entries", entries, "-of", "csv=p=0", flv]
            try: return float(subprocess.run(cmd, capture_output=True, text=True).stdout.strip())
            except Exception: return 0
        dur = _probe("format=duration") or _probe("stream=duration", "v:0")
        if not dur:
            try:
                d = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "v:0", "-count_packets",
                                    "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", flv],
                                   capture_output=True, text=True)
                dur = int(d.stdout.strip().splitlines()[-1]) / fps
            except Exception:
                dur = 0
    total = int(dur * fps) if dur else 0

    # 输出分辨率(--resize: "宽x高" 或缩放系数);与原分辨率相同则视为不缩放
    resize_str = None
    if resize:
        if "x" in str(resize).lower():
            rw, rh = str(resize).lower().split("x")[:2]; resize_str = f"{int(rw)}x{int(rh)}"
        else:
            sc = float(resize); resize_str = f"{int(W*sc)}x{int(H*sc)}"
        if resize_str == f"{W}x{H}": resize_str = None

    # 解析 ASS：R2L 样式(字体名/字号/粗体) + 拆 textonly / 特殊弹幕
    txt = open(ass, encoding="utf-8").read().splitlines()
    fontname, fs, bold = "Microsoft YaHei", 60, True
    for l in txt:
        if l.startswith("Style: R2L"):
            ps = l[len("Style:"):].split(",")
            fontname = ps[1].strip(); fs = int(ps[2]); bold = (ps[7].strip() == "-1")
            break
    textfont = resolve_font(fontname, bold)
    out_res = f" 输出{resize_str}" if resize_str else ""
    print(f"输入: {os.path.basename(flv)} {W}x{H}@{fps:.0f}fps 时长{fmt(dur)}{out_res} | 并行{workers}路 段x{segmul} 硬解{use_hwaccel}")
    print(f"文字字体: {fontname}{'(粗)' if bold else ''} -> {os.path.basename(textfont)} | emoji字体: {os.path.basename(emoji_font)} | 表情包: {len(pack)}个")

    header = [l for l in txt if not l.startswith("Dialogue")]
    dia_textonly = []; emoji_all = []
    drop_pill = False   # 标记:下一条 libass 药丸行(紧跟刚入精灵的弹幕)要丢弃,改在精灵里重画
    for l in txt:
        if not l.startswith("Dialogue"): continue
        f = l.split(",", 9)
        st, et = ass_time(f[1]), ass_time(f[2])
        effect = f[8].strip() if len(f) > 8 else ""
        if effect == "pilldraw":          # asswriter 写的 libass 药丸描边行
            if drop_pill:
                drop_pill = False; continue          # 属于走精灵的弹幕 → 丢弃(精灵里按真实宽重画)
            dia_textonly.append((st, et, l)); continue   # 纯文字/普通弹幕的药丸 → 交 libass 画
        # 只有含 emoji/表情(需 emoji 引擎)的弹幕才走精灵;纯文字(含纯文字药丸)留给 libass
        if f[3] == "R2L" and has_special(f[9], pack):
            name = f[4].strip() if len(f) > 4 else ""
            pill = parse_pill(name)
            body = f[9]; mv = re.search(r"\\move\(([^)]*)\)", body)
            if mv:
                x0, y0, x1, y1 = [float(x) for x in mv.group(1).split(",")[:4]]
                cm = re.search(r"\\1c&H([0-9A-Fa-f]{6})&", body)
                rgb = [int(cm.group(1)[4:6],16), int(cm.group(1)[2:4],16), int(cm.group(1)[0:2],16)] if cm else [255,255,255]
                am = re.search(r"\\alpha&H([0-9A-Fa-f]{2})&", body)
                aa = int(am.group(1),16) if am else 0x39
                text = re.sub(r"\{[^}]*\}", "", body)
                text = text.replace("\\N", " ").replace("\\n", " ").replace("\\h", " ").strip().translate(_STRIP)
                emoji_all.append(dict(s=st, e=et, x0=x0, y0=y0, x1=x1, text=text, rgb=rgb, op=(255-aa)/255, pill=pill))
                if pill: drop_pill = True   # 这条带药丸 → 紧随其后的 libass 药丸行要丢弃
                continue
        dia_textonly.append((st, et, l))
    print(f"特殊弹幕(emoji/表情): {len(emoji_all)} 行")

    # 字幕 ass（可选）：解析成 header + dialogue 列表，后面按段切分、与弹幕同遍 libass 烧
    sub_header, sub_dia = [], []
    if subtitle_ass and os.path.exists(subtitle_ass):
        stx = open(subtitle_ass, encoding="utf-8").read().splitlines()
        sub_header = [l for l in stx if not l.startswith("Dialogue")]
        for l in stx:
            if not l.startswith("Dialogue"):
                continue
            sf = l.split(",", 9)
            sub_dia.append((ass_time(sf[1]), ass_time(sf[2]), l))
        print(f"字幕: {len(sub_dia)} 行")

    seg_time = max(20.0, dur / max(1, workers * segmul)) if dur else 120.0
    print(f"切段中（每段约 {fmt(seg_time)}）...")
    seg_in = ["-fflags", "+genpts+discardcorrupt", "-i", flv, "-map", "0:v"]
    def seg_out(ext):
        return ["-f", "segment", "-segment_time", f"{seg_time}", "-reset_timestamps", "1",
                "-segment_list", "segs.csv", "-segment_list_type", "csv", f"seg_%03d.{ext}"]
    # 主路径：无损 -c copy 切 .ts（最快）
    r = subprocess.run([FFMPEG, "-v", "error", "-y"] + seg_in + ["-c", "copy"] + seg_out("ts"), cwd=work)
    if r.returncode != 0:
        # 源文件损坏时，-c copy 切 .ts 会触发 h264_mp4toannexb 在坏 NAL 处崩。
        # 回退1：改切 .mkv（仍 -c copy，无损且快）——mkv 不做 annexb 转换，坏包原样穿过，
        #        留待后面每段解码时由 discardcorrupt 丢弃坏帧（与 dmrender 的容错一致）。
        print("无损切段(.ts)失败（源可能损坏），回退为切 .mkv（仍无损）...")
        r = subprocess.run([FFMPEG, "-v", "error", "-y"] + seg_in + ["-c", "copy"] + seg_out("mkv"), cwd=work)
    if r.returncode != 0:
        # 回退2（极少触发）：软解 + 忽略坏帧 + 重编码切段，牺牲一代画质保出片。
        print("切 .mkv 仍失败，回退为重编码切段...")
        r = subprocess.run([FFMPEG, "-v", "error", "-y", "-err_detect", "ignore_err"]
                           + seg_in + ["-c:v", vencoder, "-b:v", vb] + seg_out("ts"), cwd=work)
    if r.returncode != 0:
        print("切段失败"); sys.exit(1)
    # 段时间不用 csv（直播源时间戳不规则/有起点偏移时，csv 的 start/end 会与段文件实际时长对不上，
    # 尤其第一段；跨段弹幕按错误时间点重定位 → 段边界整体跳）。改用各段「实际时长」累计出 off/send，
    # 让弹幕时间轴与拼接后的视频时间轴严格对齐（干净源上 csv==实际，无副作用）。
    segs = []
    cum = 0.0
    for line in open(os.path.join(work, "segs.csv"), encoding="utf-8"):
        parts = line.strip().split(",")
        if not parts or not parts[0].strip():
            continue
        segfile = parts[0].strip()
        d = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
                            "-of", "csv=p=0", os.path.join(work, segfile)],
                           capture_output=True, text=True).stdout.strip()
        try:
            seg_dur = float(d)
        except ValueError:    # 探测失败兜底用 csv 跨度
            seg_dur = (float(parts[2]) - float(parts[1])) if len(parts) >= 3 else 0.0
        segs.append((segfile, cum, cum + seg_dur))
        cum += seg_dur
    print(f"共 {len(segs)} 段")

    jobs = []
    for idx, (segfile, sstart, send) in enumerate(segs):
        off = sstart
        lines = list(header)
        for st, et, l in dia_textonly:
            if et <= off or st >= send: continue
            f = l.split(",", 9)
            nst, net = st - off, et - off
            body = f[9]
            if nst < 0:
                # 跨段进来:开始时间被钳到 0。\move 和 \fad 都相对"开始时间"，必须按"到本段起点已过的时间 elapsed"
                # 重定，否则会重新滑入(move)或重新淡入闪一下(fad)。
                elapsed = (off - st) * 1000.0   # 毫秒
                # --- \move：同时匹配 4 参数 \move(x0,y0,x1,y1) 和 6 参数 \move(...,t0,t1)(SC/醒目留言用后者) ---
                mv = re.search(r"\\move\(\s*(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)\s*(?:,\s*(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)\s*)?\)", body)
                if mv and et > st:
                    x0, y0, x1, y1 = (float(mv.group(i)) for i in range(1, 5))
                    if mv.group(5) is not None:
                        # 6 参数:运动只在 [t0,t1](毫秒)内发生。按 elapsed 重算位置和剩余时间。
                        t0, t1 = float(mv.group(5)), float(mv.group(6))
                        if elapsed >= t1:                       # 运动早已结束 → 本段静止在终点(SC 的情形)
                            new_mv = f"\\pos({x1:.0f},{y1:.0f})"
                        elif elapsed <= t0:                     # 还没开始 → 起点不变，时间整体前移
                            new_mv = f"\\move({x0:.0f},{y0:.0f},{x1:.0f},{y1:.0f},{t0-elapsed:.0f},{t1-elapsed:.0f})"
                        else:                                    # 运动到一半 → 起点 lerp 到当前位置，剩余时间从 0 起
                            fr = (elapsed - t0) / (t1 - t0)
                            cx = x0 + (x1 - x0) * fr; cy = y0 + (y1 - y0) * fr
                            new_mv = f"\\move({cx:.0f},{cy:.0f},{x1:.0f},{y1:.0f},0,{t1-elapsed:.0f})"
                        body = body[:mv.start()] + new_mv + body[mv.end():]
                    else:
                        # 4 参数:运动跨整条弹幕时长(ASS 默认) → 按时长比例算起点(原逻辑)
                        frac = (off - st) / (et - st)
                        nx0 = x0 + (x1 - x0) * frac; ny0 = y0 + (y1 - y0) * frac
                        body = body[:mv.start()] + f"\\move({nx0:.0f},{ny0:.0f},{x1:.0f},{y1:.0f})" + body[mv.end():]
                # --- \fad(淡入,淡出)：淡入相对开始 → 扣掉 elapsed(SC 早已淡完→变 0 不再淡)；淡出相对结束 → 不动 ---
                fd = re.search(r"\\fad\(\s*(\d+\.?\d*)\s*,\s*(\d+\.?\d*)\s*\)", body)
                if fd:
                    fin, fout = float(fd.group(1)), float(fd.group(2))
                    new_fin = max(0.0, fin - elapsed)
                    body = body[:fd.start()] + f"\\fad({new_fin:.0f},{fout:.0f})" + body[fd.end():]
                nst = 0.0
            f[1] = sec_to_ass(nst); f[2] = sec_to_ass(net); f[9] = body
            lines.append(",".join(f))
        ass_k = os.path.join(work, f"textonly_{idx:03d}.ass")
        open(ass_k, "w", encoding="utf-8").write("\n".join(lines))
        # 字幕按段切分 + 重定时（静态字幕，无 \move，只需窗口过滤 + 平移时间）
        sub_k = None
        if sub_dia:
            slines = list(sub_header)
            for st, et, l in sub_dia:
                if et <= off or st >= send:
                    continue
                sf = l.split(",", 9)
                sf[1] = sec_to_ass(max(0.0, st - off)); sf[2] = sec_to_ass(et - off)
                slines.append(",".join(sf))
            sub_k = os.path.join(work, f"subtitle_{idx:03d}.ass")
            open(sub_k, "w", encoding="utf-8").write("\n".join(slines))
        ev_k = []
        for ev in emoji_all:
            if ev["e"] <= off or ev["s"] >= send: continue
            e2 = dict(ev); e2["s"] = ev["s"] - off; e2["e"] = ev["e"] - off; ev_k.append(e2)
        out_ts = os.path.join(work, f"done_{idx:03d}.ts")
        prog = os.path.join(work, f"prog_{idx:03d}.txt"); open(prog, "w").write("0")
        cfg = dict(seg=os.path.join(work, segfile), textonly=ass_k, subtitle=sub_k, out=out_ts, prog=prog,
                   W=W, H=H, fps=fps, vb=vb, venc=vencoder, hwaccel=use_hwaccel, fs=fs,
                   resize=resize_str,
                   textfont=textfont, emojifont=emoji_font, pack=pack, events=ev_k)
        cfg_path = os.path.join(work, f"cfg_{idx:03d}.json")
        # ensure_ascii=True：emoji 写成 \uXXXX 纯 ASCII，彻底杜绝读取时的编码错误
        with open(cfg_path, "w", encoding="utf-8") as fcfg:
            json.dump(cfg, fcfg)
        jobs.append(dict(idx=idx, cfg=cfg_path, prog=prog))

    print("开始并行渲染（Ctrl+C 停止）...\n")
    procs = {}; pending = list(jobs); running = []
    t0 = time.time(); CREATE_GROUP = 0x00000200 if os.name == "nt" else 0
    def launch(job):
        return subprocess.Popen([sys.executable, os.path.abspath(__file__), "--worker", job["cfg"]],
                                creationflags=CREATE_GROUP)
    def killall():
        for pr in procs.values():
            try:
                if os.name == "nt": subprocess.run(["taskkill", "/F", "/T", "/PID", str(pr.pid)], capture_output=True)
                else: pr.terminate()
            except Exception: pass

    STALL = 180   # 某段进度 180 秒无推进 → 判定卡死,杀掉并标记失败
    failed = set()
    pstate = {}   # idx -> [last_n, last_t]
    _prog_last = {}
    def prog_n(job):
        try:
            v = int(open(job["prog"]).read().split()[0]); _prog_last[job["idx"]] = v; return v
        except Exception:
            return _prog_last.get(job["idx"], 0)   # 偶发读失败时返回上次值,避免进度回跳
    def prog_done(job):
        try: return open(job["prog"]).read().strip().endswith("done")
        except Exception: return False
    try:
        last = 0.0
        while pending or running:
            while pending and len(running) < workers:
                job = pending.pop(0); pr = launch(job); procs[job["idx"]] = pr
                pstate[job["idx"]] = [0, time.time()]; running.append((job, pr))
            still = []
            for job, pr in running:
                rc = pr.poll()
                if rc is None:
                    n_now = prog_n(job); st = pstate[job["idx"]]
                    if n_now > st[0]: st[0] = n_now; st[1] = time.time()
                    elif time.time() - st[1] > STALL:
                        try: subprocess.run(["taskkill", "/F", "/T", "/PID", str(pr.pid)], capture_output=True)
                        except Exception: pass
                        failed.add(job["idx"]); continue
                    still.append((job, pr))
                else:   # 已退出：检查是否正常完成
                    if not prog_done(job): failed.add(job["idx"])
            running = still
            now = time.time()
            if now - last >= 0.5:
                last = now
                done = sum(prog_n(job) for job in jobs)
                el = now - t0; cur = done / el if el > 0 else 0
                fl = f" 失败{len(failed)}段" if failed else ""
                if total:
                    dtot = max(total, done)   # 估算帧数偏少时动态抬高,避免显示超过100%/负剩余
                    pct = done / dtot * 100; eta = max(0.0, (dtot - done) / cur) if cur > 0 else 0; bn = int(pct / 100 * 20)
                    sys.stdout.write(f"\r[{'#'*bn}{'-'*(20-bn)}] {pct:5.1f}%  {done}/{dtot}帧  {cur:.0f}fps  已用{fmt(el)} 剩~{fmt(eta)}{fl}   ")
                else:
                    sys.stdout.write(f"\r{done}帧  {cur:.0f}fps  已用{fmt(el)}{fl}   ")
                sys.stdout.flush()
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("\n\n收到停止信号,结束所有子进程..."); killall(); shutil.rmtree(work, ignore_errors=True)
        print("已停止。"); return
    for pr in procs.values():
        try: pr.wait(timeout=5)
        except Exception:
            try: subprocess.run(["taskkill", "/F", "/T", "/PID", str(pr.pid)], capture_output=True)
            except Exception: pass
    print()

    # 校验：每段都要有 done_*.ts，否则报错退出(不产出残缺视频、不卡死)
    missing = [j["idx"] for j in jobs if not os.path.exists(os.path.join(work, f"done_{j['idx']:03d}.ts"))]
    bad = sorted(failed | set(missing))
    if bad:
        print(f"渲染失败的分段: {bad}\n中间文件保留在: {work}\n(可重跑;若反复失败,把对应 cfg_NNN.json / 该段报错发我)")
        sys.exit(1)

    dones = sorted(glob.glob(os.path.join(work, "done_*.ts")))
    with open(os.path.join(work, "concat.txt"), "w", encoding="utf-8") as fo:
        for d in dones: fo.write(f"file '{os.path.basename(d)}'\n")
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "concat", "-safe", "0",
                    "-i", "concat.txt", "-c", "copy", "full.ts"], cwd=work)
    print("拼接完成,混音中...")
    full = os.path.join(work, "full.ts")
    # 源音频本就是 aac 时直接 copy,免去一次重编码(更快、无损)
    a_args = ["-c:a", "copy"] if audio_codec(flv) == "aac" else ["-c:a", "aac", "-b:a", ab]
    r = subprocess.run([FFMPEG, "-v", "error", "-y", "-i", full, "-i", flv,
                        "-map", "0:v", "-map", "1:a?", "-c:v", "copy", *a_args,
                        "-shortest", out_mp4])
    dt = time.time() - t0
    if r.returncode == 0:
        shutil.rmtree(work, ignore_errors=True)
        real_frames = sum(prog_n(job) for job in jobs) or total   # 用真实渲染帧数算平均,更准
        avg = real_frames / dt if dt > 0 else 0
        print(f"\n完成！用时 {fmt(dt)}（平均 {avg:.0f}fps）,输出: {out_mp4}")
    else:
        print(f"混音失败,中间文件保留在: {work}")


if __name__ == "__main__":
    main()
