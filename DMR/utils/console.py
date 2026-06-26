"""终端显示助手：ANSI 颜色表 + 等宽对齐（按显示宽度，兼容中日韩全角字符）。
被 Downloader（stream_downloader / __init__）用于控制台进度排版。"""
from wcwidth import wcswidth

COLORS = {
    "gray":   "\033[90m",
    "green":  "\033[32m",
    "yellow": "\033[33m",
    "red":    "\033[31m",
    "blue":   "\033[34m",
    "reset":  "\033[0m",
}


def pad_disp(s: str, width: int, align: str = "left", fill: str = " "):
    s = str(s)
    w = wcswidth(s)
    if w < 0:  # 遇到不可见控制符等，兜底
        w = len(s)

    pad = max(0, width - w)

    if align == "left":
        return s + fill * pad
    elif align == "right":
        return fill * pad + s
    elif align == "center":
        left = pad // 2
        return fill * left + s + fill * (pad - left)
    else:
        raise ValueError("align must be left/right/center")
