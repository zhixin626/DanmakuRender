# -*- coding: utf-8 -*-
"""弹幕宽度测量(PIL + 字体回退链 + 标定)与药丸(stadium)描边绘制。

为什么需要:libass 渲染弹幕的实际像素宽事先无法知道。旧的"ASCII 半宽 / 其余全宽"
估算对比例字体(W vs i)和命名字体覆盖不到的字符(楔形文字/生僻字/emoji)能错好几倍。
这里用 PIL getlength 逐字符按"字体回退链"测量(覆盖不到就换下一个字体,模拟 libass
的 font fallback),再乘标定系数,误差约 2~3%。详见 test/width_probe.py。
"""
import os
from functools import lru_cache

__all__ = ["TextMeasurer", "pill_path", "make_pill_line", "CALIB"]

# libass 在 Fontsize=S 时的实际字宽 ≈ PIL@S × 0.747(实测,字形无关的线性系数)
CALIB = 0.747

# Windows 默认字体回退链(按系统大致回退顺序):正文 → 符号 → 历史字符(楔形等) → emoji
_WIN_FALLBACK = [
    r"C:\Windows\Fonts\seguisym.ttf",   # Segoe UI Symbol
    r"C:\Windows\Fonts\seguihis.ttf",   # Segoe UI Historic(楔形文字等冷门脚本)
    r"C:\Windows\Fonts\seguiemj.ttf",   # Segoe UI Emoji
]
_EMOJI_FONT_BASENAME = 'seguiemj.ttf'   # Unicode Emoji 测宽优先使用的字体

# ---- 以下两张表与 emoji_render_mt.py 的 is_emoji / _STRIP 保持一致(改任一处要同步另一处) ----
# 组合/修饰码点不单独占用步进宽，测宽时移除。
_EMOJI_STRIP = {0x200D: None, 0x20E3: None}                  # ZWJ、keycap 封闭符
_EMOJI_STRIP.update({c: None for c in range(0xFE00, 0xFE10)})     # 变体选择符
_EMOJI_STRIP.update({c: None for c in range(0x1F3FB, 0x1F400)})   # 肤色修饰符


def _is_emoji_cp(o: int) -> bool:
    """该码点是否会被精灵引擎当 emoji 用 emoji 字体绘制(同 emoji_render_mt.is_emoji)。"""
    return (0x1F300 <= o <= 0x1FAFF) or (0x1F000 <= o <= 0x1F0FF) \
        or (0x2600 <= o <= 0x27BF) or (0x2300 <= o <= 0x23FF) or (0x2B00 <= o <= 0x2BFF) \
        or (0x1F1E6 <= o <= 0x1F1FF) \
        or (0xFE00 <= o <= 0xFE0F) or (0x1F3FB <= o <= 0x1F3FF) or o == 0x200D or o == 0x20E3


class TextMeasurer:
    """预测 libass 渲染后的像素宽。逐字符挑回退链里第一个含该字形的字体测步进宽。"""

    def __init__(self, font_family, fontsize, bold=True, calib=CALIB,
                 fallback=None, cache_limit=50000):
        from PIL import ImageFont
        self.fontsize = int(fontsize)
        self.calib = calib
        self.cache_limit = int(cache_limit)
        self._cache = {}
        self._font_by_cp = {}
        px = max(1, round(self.fontsize * calib))

        # 主字体路径(matplotlib 解析字体名;失败则兜底雅黑)
        try:
            import matplotlib.font_manager as fm
            primary = fm.findfont(fm.FontProperties(
                family=font_family, weight="bold" if bold else "normal"),
                fallback_to_default=True)
        except Exception:
            primary = r"C:\Windows\Fonts\msyhbd.ttc" if bold else r"C:\Windows\Fonts\msyh.ttc"

        paths = [primary] + list(_WIN_FALLBACK if fallback is None else fallback)
        self.chain = []   # [(ImageFont, coverage_set|None)]
        seen = set()
        for p in paths:
            if not p:
                continue
            ap = os.path.abspath(p)
            if ap in seen or not os.path.isfile(p):
                continue
            seen.add(ap)
            try:
                f = ImageFont.truetype(ap, px)
            except Exception:
                continue
            self.chain.append((f, self._coverage(ap)))
        if not self.chain:
            self.chain.append((ImageFont.load_default(), None))
        self._default = self.chain[0][0]
        # Emoji 码点优先使用 Segoe UI Emoji。不能直接采用回退链中的第一个
        # 覆盖字体：Segoe UI Symbol 也覆盖大量 Emoji，但字距通常明显更窄。
        self._emoji_font = next(
            (f for f, _ in self.chain
             if os.path.basename(getattr(f, 'path', '') or '').lower() == _EMOJI_FONT_BASENAME),
            None)

    @staticmethod
    @lru_cache(maxsize=32)
    def _coverage(path):
        """字体覆盖的码点集合；跨实例缓存，避免每个视频段重复解析字体 cmap。"""
        try:
            from fontTools.ttLib import TTFont
            tt = TTFont(path, fontNumber=0, lazy=True)
            cov = set()
            for tb in tt["cmap"].tables:
                cov |= set(tb.cmap.keys())
            tt.close()
            return frozenset(cov)
        except Exception:
            return None

    def _font_for(self, cp):
        cached = self._font_by_cp.get(cp)
        if cached is not None:
            return cached

        # Emoji 码点直选专用字体，避免被更靠前的 Symbol 字体截获。
        if self._emoji_font is not None and _is_emoji_cp(cp):
            font = self._emoji_font
        else:
            font = next(
                (candidate for candidate, cov in self.chain if cov is None or cp in cov),
                self._default,
            )

        # 常见字符会跨大量弹幕反复出现；缓存选择结果，避免每次重新遍历字体覆盖表。
        if len(self._font_by_cp) >= 8192:
            self._font_by_cp.clear()
        self._font_by_cp[cp] = font
        return font

    def measure(self, text):
        """预测 libass 渲染后的像素宽。

        emoji_dmrender 的精灵行由 EmojiWidthMeasurer 测量；这里专注于普通
        ASS 文字，以及 SC/礼物盒子中的 libass 文字。
        """
        if not text:
            return 0
        # 组合/修饰码点在当前渲染链中不单独占用步进宽，测宽时统一移除。
        text = text.translate(_EMOJI_STRIP)
        if not text:
            return 0
        cached = self._cache.get(text)
        if cached is not None:
            return cached
        total = 0.0
        i, n = 0, len(text)
        while i < n:                       # 按"连续同字体"分段,整段 getlength
            cp = ord(text[i])
            f = self._font_for(cp)
            j = i + 1
            while j < n and self._font_for(ord(text[j])) is f:
                j += 1
            total += f.getlength(text[i:j])
            i = j
        w = int(round(total))
        if len(self._cache) >= self.cache_limit:   # 带上限,长直播也不爆内存
            self._cache.clear()
        self._cache[text] = w
        return w

    def _char_w(self, ch):
        """单字符步进宽(像素,未折算)。"""
        return self._font_for(ord(ch)).getlength(ch)

    def wrap(self, text, budget_px):
        """按像素预算逐字符断行,返回行列表(不含换行符)。
        用于固定宽度盒子(SC/礼物)的内容排版——盒子由 libass 渲染,不做精灵折算。"""
        if not text:
            return []
        text = text.translate(_EMOJI_STRIP)
        lines, cur, cur_w = [], '', 0.0
        for ch in text:
            w = self._char_w(ch)
            if cur and cur_w + w > budget_px:
                lines.append(cur)
                cur, cur_w = ch, w
            else:
                cur += ch
                cur_w += w
        if cur:
            lines.append(cur)
        return lines

    def truncate(self, text, budget_px, ellipsis='…'):
        """超出像素预算则截断到最长前缀并加省略号;不超原样返回(libass 口径,不折算)。
        用于盒子上框的用户名——名字不折行,截断保布局。"""
        if not text:
            return text
        text = text.translate(_EMOJI_STRIP)
        if self.measure(text) <= budget_px:
            return text
        # `ellipsis` may be either a single glyph ("…") or a string ("...").
        # Measure the complete marker instead of passing it through ord().
        budget = budget_px - self.measure(ellipsis)
        out, cur_w = '', 0.0
        for ch in text:
            w = self._char_w(ch)
            if cur_w + w > budget:
                break
            out += ch
            cur_w += w
        return out + ellipsis


def pill_path(W, H):
    """ASS \\p1 药丸(stadium)闭合路径:左右两端半圆,圆角半径=H/2,左上角为(0,0)。"""
    r = H / 2.0
    k = 0.5522847498 * r                   # 四分之一圆的贝塞尔控制点系数
    def P(x, y): return f"{round(x)} {round(y)}"
    A = (r, 0); B = (W - r, 0); C = (W - r, H); D = (r, H); M = (W, r); N = (0, r)
    return " ".join([
        f"m {P(*A)}", f"l {P(*B)}",
        f"b {P(B[0] + k, B[1])} {P(M[0], M[1] - k)} {P(*M)}",   # 右上 1/4
        f"b {P(M[0], M[1] + k)} {P(C[0] + k, C[1])} {P(*C)}",   # 右下 1/4
        f"l {P(*D)}",
        f"b {P(D[0] - k, D[1])} {P(N[0], N[1] + k)} {P(*N)}",   # 左下 1/4
        f"b {P(N[0], N[1] - k)} {P(A[0] - k, A[1])} {P(*A)}",   # 左上 1/4
    ])


def make_pill_line(t0, t1, x0, x1, y_top, width, height, *,
                   color="FFC0CB", border=3, opacity="00", layer=0, style="R2L", effect=""):
    """生成一条"只有描边、中间透明"的药丸 Dialogue 行,用 \\move 与文字同步滚动。

    x0/x1 : 药丸左上角(\\an7)起止 x(从右侧进、左侧出)
    y_top : 药丸顶部 y
    width/height : 药丸尺寸(像素)
    color : 描边色 RRGGBB(默认淡粉);opacity : 描边透明度 2 位十六进制(00=不透明)
    effect : Effect 字段标记(如 'pilldraw',供 emoji 渲染器识别这是药丸描边行)
    """
    bgr = color[4:6] + color[2:4] + color[0:2]
    tags = (f"\\an7\\move({x0:.0f},{y_top:.0f},{x1:.0f},{y_top:.0f})"
            f"\\p1\\bord{border}\\shad0"
            f"\\1a&HFF&\\3a&H{opacity}&\\3c&H{bgr}&\\4a&HFF&")   # 填充全透明,只留描边
    return f"Dialogue: {layer},{t0},{t1},{style},,0,0,0,{effect},{{{tags}}}{pill_path(width, height)}"
