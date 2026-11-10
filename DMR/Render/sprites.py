"""Shared Pillow sprite construction for the emoji renderers."""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont


log = logging.getLogger("emoji_mt")

SCALE = 0.75
OUTLINE = 1
OUT = OUTLINE
SHORTCODE_RE = re.compile(r"\[([^\[\]\r\n]{1,200})\]")
EMOTICON_PREFIX = "; DMR_EMOTICON:"
_STRIP = {0x200D: None, 0x20E3: None}
_STRIP.update({codepoint: None for codepoint in range(0xFE00, 0xFE10)})
_STRIP.update({codepoint: None for codepoint in range(0x1F3FB, 0x1F400)})


def default_emoji_font() -> str:
    """Return a usable color emoji font without requiring project assets."""
    if os.name == "nt":
        return r"C:\Windows\Fonts\seguiemj.ttf"
    for path in (
        "/System/Library/Fonts/Apple Color Emoji.ttc",
        "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
        "/usr/share/fonts/noto-cjk/NotoColorEmoji.ttf",
        "/usr/share/fonts/google-noto-emoji/NotoColorEmoji.ttf",
    ):
        if os.path.isfile(path):
            return path
    return "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"


DEFAULT_EMOJI_FONT = default_emoji_font()


def is_emoji(char: str) -> bool:
    """Return whether a code point is handled by the emoji sprite path."""
    value = ord(char)
    return (
        0x1F300 <= value <= 0x1FAFF
        or 0x1F000 <= value <= 0x1F0FF
        or 0x2600 <= value <= 0x27BF
        or 0x2300 <= value <= 0x23FF
        or 0x2B00 <= value <= 0x2BFF
        or 0x1F1E6 <= value <= 0x1F1FF
        or 0xFE00 <= value <= 0xFE0F
        or 0x1F3FB <= value <= 0x1F3FF
        or value in (0x200D, 0x20E3)
    )


def has_special(body: str, pack: dict[str, str]) -> bool:
    """Return whether a message needs a color emoji or image sprite."""
    clean = re.sub(r"\{[^}]*\}", "", body)
    if any(is_emoji(char) for char in clean):
        return True
    return bool(pack and any(match.group(1).lower() in pack for match in SHORTCODE_RE.finditer(clean)))


def tokenize(text: str, pack: dict[str, str]) -> list[tuple[str, str]]:
    """Split text into ordinary text, emoji, and image tokens."""
    tokens: list[tuple[str, str]] = []
    parts = SHORTCODE_RE.split(text)
    for index, part in enumerate(parts):
        if index % 2 == 1:
            if pack and part.lower() in pack:
                tokens.append(("png", pack[part.lower()]))
                continue
            part = f"[{part}]"
        current = ""
        current_is_emoji: bool | None = None
        for char in part:
            char_is_emoji = is_emoji(char)
            if current and char_is_emoji != current_is_emoji:
                tokens.append(("emoji" if current_is_emoji else "text", current))
                current = ""
            current += char
            current_is_emoji = char_is_emoji
        if current:
            tokens.append(("emoji" if current_is_emoji else "text", current))
    return tokens


def parse_pill(name: str | None) -> dict[str, Any] | None:
    """Parse an ASS pill marker into its drawing parameters."""
    if not name or not name.startswith("pill"):
        return None
    parts = name.split("#")
    color = parts[1] if len(parts) > 1 and len(parts[1]) == 6 else "FFC0CB"
    try:
        padding = int(parts[2]) if len(parts) > 2 else 6
    except ValueError:
        padding = 6
    try:
        border = int(parts[3]) if len(parts) > 3 else 3
    except ValueError:
        border = 3
    return {
        "color": [int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)],
        "pad": padding,
        "border": max(1, border),
    }


def resolve_font(family: str, bold: bool) -> str:
    """Resolve an ASS font family to a local font file."""
    try:
        import matplotlib.font_manager as font_manager

        properties = font_manager.FontProperties(family=family, weight="bold" if bold else "normal")
        return font_manager.findfont(properties, fallback_to_default=True)
    except Exception:
        return r"C:\Windows\Fonts\msyhbd.ttc" if bold else r"C:\Windows\Fonts\msyh.ttc"


def resolve_emoji_font(value: str | None) -> str:
    """Resolve an emoji font path or family, with a system fallback."""
    if not value:
        return DEFAULT_EMOJI_FONT
    script_dir = os.path.dirname(os.path.abspath(__file__))
    extensions = ("", ".ttf", ".ttc", ".otf")
    directories = ("", os.getcwd(), script_dir, os.path.join(script_dir, "test"), r"C:\Windows\Fonts")
    for directory in directories:
        for extension in extensions:
            candidate = os.path.join(directory, value + extension) if directory else value + extension
            if os.path.isfile(candidate):
                return os.path.abspath(candidate)
    try:
        import matplotlib.font_manager as font_manager

        candidate = font_manager.findfont(
            font_manager.FontProperties(family=value),
            fallback_to_default=False,
        )
        if candidate and os.path.isfile(candidate):
            return os.path.abspath(candidate)
    except Exception:
        pass
    log.warning("找不到 emoji 字体 '%s', 回退到系统 Segoe UI Emoji。", value)
    return DEFAULT_EMOJI_FONT


def load_pack(pack_dirs: str | None) -> dict[str, str]:
    """Load image files from comma-separated emoji pack directories."""
    pack: dict[str, str] = {}
    if not pack_dirs:
        return pack
    for directory in str(pack_dirs).split(","):
        directory = directory.strip()
        if not directory or not os.path.isdir(directory):
            continue
        for filename in os.listdir(directory):
            name, extension = os.path.splitext(filename)
            if extension.lower() in (".png", ".webp", ".gif"):
                pack[name.lower()] = os.path.abspath(os.path.join(directory, filename))
    return pack


class SpriteFactory:
    """Build and cache the exact RGBA/YUV sprites used by the old renderer."""

    def __init__(self, text_font: str, emoji_font: str, pack: dict[str, str], font_size: float):
        correction = max(1, int(round(font_size * SCALE)))
        self.text_font = ImageFont.truetype(text_font, correction)
        self.emoji_font, self.emoji_scale = self._load_emoji_font(emoji_font, correction, text_font)
        self.emoji_ascent, self.emoji_descent = self.emoji_font.getmetrics()
        self.ascent, self.descent = self.text_font.getmetrics()
        self.png_height = int(round(correction * 1.25))
        self.png_center = -int(round(self.ascent * 0.38))
        self.pack = pack
        self.png_cache: dict[str, Image.Image] = {}
        self.cache: dict[tuple[Any, ...], tuple[Any, ...]] = {}
        self.image_cache: dict[tuple[Any, ...], Image.Image] = {}

    @staticmethod
    def _load_emoji_font(path: str, correction: int, text_font: str):
        try:
            return ImageFont.truetype(path, correction), 1.0
        except OSError:
            pass
        for size in (109, 128, 136, 96, 160, 64, 32):
            try:
                font = ImageFont.truetype(path, size)
                return font, correction / font.size if getattr(font, "size", 0) else 1.0
            except OSError:
                continue
        for fallback in (DEFAULT_EMOJI_FONT, text_font):
            try:
                return ImageFont.truetype(fallback, correction), 1.0
            except OSError:
                continue
        return ImageFont.load_default(), 1.0

    def _load_png(self, path: str) -> Image.Image:
        if path not in self.png_cache:
            image = Image.open(path).convert("RGBA")
            width = max(1, int(round(image.width * self.png_height / image.height)))
            self.png_cache[path] = image.resize((width, self.png_height), Image.Resampling.LANCZOS)
        return self.png_cache[path]

    def get_sprite(
        self,
        text: str,
        rgb: tuple[int, int, int] | list[int],
        pill: dict[str, Any] | None = None,
        event_pack: dict[str, str] | None = None,
        outline: float = OUTLINE,
        outline_rgb: tuple[int, int, int] = (0, 0, 0),
    ) -> tuple[Any, ...]:
        """Return the cached sprite tuple consumed by the NV12 compositor."""
        outline = max(0, int(round(outline)))
        outline_rgb = tuple(int(value) for value in outline_rgb)
        pill_key = tuple(pill["color"] + [pill["pad"], pill["border"]]) if pill else None
        active_pack = self.pack if event_pack is None else event_pack
        event_key = None if event_pack is None else tuple(sorted(event_pack.items()))
        key = (text, tuple(rgb), pill_key, event_key, outline, outline_rgb)
        if key in self.cache:
            self._last_image = self.image_cache[key]
            return self.cache[key]

        tokens = tokenize(text, active_pack)
        has_png = any(kind == "png" for kind, _ in tokens)
        has_emoji = any(kind == "emoji" for kind, _ in tokens)
        prepared_tokens = []
        content_width = 0
        for kind, payload in tokens:
            if kind == "png":
                prepared = self._load_png(payload)
                token_width = prepared.width
            elif kind == "emoji":
                prepared = None
                token_width = max(1, int(self.emoji_font.getlength(payload) * self.emoji_scale))
            else:
                prepared = None
                token_width = max(0, int(self.text_font.getlength(payload)))
            prepared_tokens.append((kind, payload, prepared, token_width))
            content_width += token_width

        png_top = self.png_center - self.png_height // 2
        png_bottom = self.png_center + self.png_height - self.png_height // 2
        emoji_ascent = int(round(self.emoji_ascent * self.emoji_scale)) if has_emoji else 0
        emoji_descent = int(round(self.emoji_descent * self.emoji_scale)) if has_emoji else 0
        content_ascent = max(self.ascent, -png_top if has_png else 0, emoji_ascent)
        content_descent = max(self.descent, png_bottom if has_png else 0, emoji_descent)
        if pill:
            padding = pill["pad"]
            pill_height = content_ascent + content_descent + 2 * padding
            pad_x = pill_height / 2.0
            top_extent = int(round(content_ascent + padding))
            bottom_extent = int(round(content_descent + padding))
            content_x = int(round(pad_x))
            total_width = max(int(round(content_width + 2 * pad_x)), int(round(pill_height)))
            anchor_x_offset = 0
        else:
            top_extent = content_ascent + outline
            bottom_extent = content_descent + outline
            content_x = outline
            total_width = content_width + 2 * outline
            anchor_x_offset = outline

        baseline = top_extent
        sprite = Image.new("RGBA", (max(int(total_width), 1), int(top_extent + bottom_extent)), (0, 0, 0, 0))
        draw = ImageDraw.Draw(sprite)
        x = content_x
        for kind, payload, prepared, token_width in prepared_tokens:
            if kind == "png":
                sprite.alpha_composite(prepared, (int(x), baseline + png_top))
            elif kind == "emoji":
                if self.emoji_scale == 1.0:
                    draw.text((x, baseline), payload, font=self.emoji_font, embedded_color=True, anchor="ls")
                else:
                    emoji_width = max(1, int(self.emoji_font.getlength(payload)))
                    temporary = Image.new("RGBA", (emoji_width, self.emoji_ascent + self.emoji_descent), (0, 0, 0, 0))
                    ImageDraw.Draw(temporary).text((0, self.emoji_ascent), payload, font=self.emoji_font, embedded_color=True, anchor="ls")
                    temporary = temporary.resize((token_width, max(1, int((self.emoji_ascent + self.emoji_descent) * self.emoji_scale))), Image.Resampling.LANCZOS)
                    sprite.alpha_composite(temporary, (int(x), baseline - int(round(self.emoji_ascent * self.emoji_scale))))
            else:
                draw.text(
                    (x, baseline),
                    payload,
                    font=self.text_font,
                    fill=tuple(rgb) + (255,),
                    stroke_width=outline,
                    stroke_fill=outline_rgb + (255,),
                    anchor="ls",
                )
            x += token_width

        text_bottom_offset = baseline + self.descent
        if pill:
            border = pill["border"]
            color = tuple(pill["color"]) + (255,)
            x0 = border / 2.0
            x1 = sprite.width - 1 - border / 2.0
            y0 = border / 2.0
            y1 = sprite.height - 1 - border / 2.0
            draw.rounded_rectangle((x0, y0, x1, y1), radius=(y1 - y0) / 2.0, outline=color, width=border)

        values = np.asarray(sprite).astype(np.float32)
        red, green, blue = values[:, :, 0], values[:, :, 1], values[:, :, 2]
        alpha = values[:, :, 3] / 255.0
        y = 16 + 0.1826 * red + 0.6142 * green + 0.0620 * blue
        u = 128 - 0.1006 * red - 0.3386 * green + 0.4392 * blue
        v = 128 + 0.4392 * red - 0.3989 * green - 0.0403 * blue
        anchor_y_offset = baseline - self.ascent
        result = (
            y,
            u,
            v,
            alpha,
            sprite.width,
            sprite.height,
            text_bottom_offset,
            content_x,
            anchor_x_offset,
            anchor_y_offset,
        )
        self.cache[key] = result
        self.image_cache[key] = sprite.copy()
        self._last_image = sprite
        return result

    def get_sprite_image(
        self,
        text: str,
        rgb: tuple[int, int, int] | list[int],
        event_pack: dict[str, str] | None = None,
        outline: float = OUTLINE,
        outline_rgb: tuple[int, int, int] = (0, 0, 0),
    ) -> Image.Image:
        """Return the same Pillow image used to build the cached YUV sprite."""
        self.get_sprite(
            text,
            rgb,
            event_pack=event_pack,
            outline=outline,
            outline_rgb=outline_rgb,
        )
        return self._last_image.copy()
