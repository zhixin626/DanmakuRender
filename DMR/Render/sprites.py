"""Shared Pillow sprite construction for the emoji renderers."""

from __future__ import annotations

import json
import logging
import os
import pathlib
import re
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageChops, ImageFilter
from DMR.EmojiPack.common import EmoticonStore

try:
    from .layout_models import event_track
except ImportError:  # emoji_render_mt's standalone subprocess
    from layout_models import event_track


log = logging.getLogger("emoji_mt")

SCALE = 0.75
SUPERSAMPLE = 4
OUTLINE = 1
OUT = OUTLINE
SHORTCODE_RE = re.compile(r"\[([^\[\]\r\n]{1,200})\]")

SCROLL_SPRITE_SIDE_MARGIN = 4
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
    return bool(
        pack
        and any(
            match.group(1).lower() in pack for match in SHORTCODE_RE.finditer(clean)
        )
    )


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

        properties = font_manager.FontProperties(
            family=family, weight="bold" if bold else "normal"
        )
        return font_manager.findfont(properties, fallback_to_default=False)
    except (ImportError, ValueError, OSError) as error:
        log.warning("找不到字体 '%s'（%s），回退到支持中文的系统字体。", family, error)
    if os.name == "nt":
        candidates = [
            r"C:\Windows\Fonts\msyhbd.ttc" if bold else r"C:\Windows\Fonts\msyh.ttc"
        ]
    else:
        candidates = [
            "/System/Library/Fonts/PingFang.ttc",
            (
                "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
                if bold
                else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
            ),
        ]
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    raise FileNotFoundError(
        f"字体 {family!r} 不存在，也找不到支持中文的系统字体；请配置已安装字体"
    )


def resolve_emoji_font(value: str | None) -> str:
    """Resolve an emoji font path or family, with a system fallback."""
    if not value:
        return DEFAULT_EMOJI_FONT
    script_dir = os.path.dirname(os.path.abspath(__file__))
    extensions = ("", ".ttf", ".ttc", ".otf")
    directories = (
        "",
        os.getcwd(),
        script_dir,
        os.path.join(script_dir, "test"),
        r"C:\Windows\Fonts",
    )
    for directory in directories:
        for extension in extensions:
            candidate = (
                os.path.join(directory, value + extension)
                if directory
                else value + extension
            )
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
    except Exception as error:
        log.debug("按字体名查找 Emoji 字体失败: %s", error)
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

    def __init__(
        self, text_font: str, emoji_font: str, pack: dict[str, str], font_size: float
    ):
        self.font_size = float(font_size)
        correction = max(1, int(round(font_size * SCALE)))
        self.text_font = ImageFont.truetype(text_font, correction)
        self.emoji_font, self.emoji_scale = self._load_emoji_font(
            emoji_font, correction, text_font
        )
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
            self.png_cache[path] = image.resize(
                (width, self.png_height), Image.Resampling.LANCZOS
            )
        return self.png_cache[path]

    def text_layout(self, text: str, pack: dict[str, str]):
        """One font/token layout shared by measurement and pixel drawing."""
        tokens = tokenize(text, pack)
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
                token_width = max(
                    1, int(self.emoji_font.getlength(payload) * self.emoji_scale)
                )
            else:
                prepared = None
                token_width = max(0, int(self.text_font.getlength(payload)))
            prepared_tokens.append((kind, payload, prepared, token_width))
            content_width += token_width

        png_top = self.png_center - self.png_height // 2
        png_bottom = self.png_center + self.png_height - self.png_height // 2
        emoji_ascent = (
            int(round(self.emoji_ascent * self.emoji_scale)) if has_emoji else 0
        )
        emoji_descent = (
            int(round(self.emoji_descent * self.emoji_scale)) if has_emoji else 0
        )
        content_ascent = max(self.ascent, -png_top if has_png else 0, emoji_ascent)
        content_descent = max(self.descent, png_bottom if has_png else 0, emoji_descent)
        return prepared_tokens, content_width, content_ascent, content_descent

    def measure(
        self, text: str, outline: float = OUTLINE, event_pack=None
    ) -> tuple[int, int]:
        pack = self.pack if event_pack is None else event_pack
        _, width, ascent, descent = self.text_layout(text, pack)
        border = max(0, round(outline))
        return max(1, width + 2 * border), ascent + descent + 2 * border

    def get_sprite(
        self,
        text: str,
        rgb: tuple[int, int, int] | list[int],
        pill: dict[str, Any] | None = None,
        event_pack: dict[str, str] | None = None,
        outline: float = OUTLINE,
        outline_rgb: tuple[int, int, int] = (0, 0, 0),
        scroll_vertical_gap: float | None = None,
    ) -> tuple[Any, ...]:
        """Return the cached sprite tuple consumed by the NV12 compositor."""
        outline = max(0, int(round(outline)))
        outline_rgb = tuple(int(value) for value in outline_rgb)
        pill_key = (
            tuple(pill["color"] + [pill["pad"], pill["border"]]) if pill else None
        )
        active_pack = self.pack if event_pack is None else event_pack
        event_key = None if event_pack is None else tuple(sorted(event_pack.items()))
        key = (
            text,
            tuple(rgb),
            pill_key,
            event_key,
            outline,
            outline_rgb,
            scroll_vertical_gap,
        )
        if key in self.cache:
            self._last_image = self.image_cache[key]
            return self.cache[key]

        prepared_tokens, content_width, content_ascent, content_descent = (
            self.text_layout(text, active_pack)
        )
        if pill:
            padding = pill["pad"]
            pill_height = content_ascent + content_descent + 2 * padding
            pad_x = pill_height / 2.0
            top_extent = int(round(content_ascent + padding))
            bottom_extent = int(round(content_descent + padding))
            content_x = int(round(pad_x))
            total_width = max(
                int(round(content_width + 2 * pad_x)), int(round(pill_height))
            )
            anchor_x_offset = 0
        else:
            vertical_margin = (
                min(
                    max(0, int(round(self.font_size * 0.2))),
                    max(0, int(float(scroll_vertical_gap or 0) // 2)),
                )
                if scroll_vertical_gap is not None
                else 0
            )
            side_margin = (
                SCROLL_SPRITE_SIDE_MARGIN
                if scroll_vertical_gap is not None
                else 0
            )
            top_extent = content_ascent + outline + vertical_margin
            bottom_extent = content_descent + outline + vertical_margin
            content_x = outline + side_margin
            total_width = content_width + 2 * (outline + side_margin)
            anchor_x_offset = outline + side_margin

        baseline = top_extent
        sprite = Image.new(
            "RGBA",
            (max(int(total_width), 1), int(top_extent + bottom_extent)),
            (0, 0, 0, 0),
        )
        draw = ImageDraw.Draw(sprite)
        x = content_x
        png_top = self.png_center - self.png_height // 2
        for kind, payload, prepared, token_width in prepared_tokens:
            if kind == "png":
                sprite.alpha_composite(prepared, (int(x), baseline + png_top))
            elif kind == "emoji":
                if self.emoji_scale == 1.0:
                    draw.text(
                        (x, baseline),
                        payload,
                        font=self.emoji_font,
                        embedded_color=True,
                        anchor="ls",
                    )
                else:
                    emoji_width = max(1, int(self.emoji_font.getlength(payload)))
                    temporary = Image.new(
                        "RGBA",
                        (emoji_width, self.emoji_ascent + self.emoji_descent),
                        (0, 0, 0, 0),
                    )
                    ImageDraw.Draw(temporary).text(
                        (0, self.emoji_ascent),
                        payload,
                        font=self.emoji_font,
                        embedded_color=True,
                        anchor="ls",
                    )
                    temporary = temporary.resize(
                        (
                            token_width,
                            max(
                                1,
                                int(
                                    (self.emoji_ascent + self.emoji_descent)
                                    * self.emoji_scale
                                ),
                            ),
                        ),
                        Image.Resampling.LANCZOS,
                    )
                    sprite.alpha_composite(
                        temporary,
                        (
                            int(x),
                            baseline - int(round(self.emoji_ascent * self.emoji_scale)),
                        ),
                    )
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
            border_image = draw_round(
                sprite.size, sprite.height / 2, "stadium", outline=color, border=border
            )
            sprite.alpha_composite(border_image)

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
        fit_width: int | None = None,
        scroll_vertical_gap: float | None = None,
    ) -> Image.Image:
        """Return the same Pillow image used to build the cached YUV sprite."""
        self.get_sprite(
            text,
            rgb,
            event_pack=event_pack,
            outline=outline,
            outline_rgb=outline_rgb,
            scroll_vertical_gap=scroll_vertical_gap,
        )
        image = self._last_image.copy()
        if fit_width is not None and image.width > fit_width:
            target = (
                max(1, fit_width),
                max(1, round(image.height * fit_width / image.width)),
            )
            image = image.resize(target, Image.Resampling.LANCZOS)
        return image


class PillowText:
    """Measure/wrap with exactly the font, tokens and outline used to draw."""

    def __init__(
        self, font: str, size: float, outline: float = 0, pack=None, emoji_font=None
    ):
        self.factory = SpriteFactory(
            resolve_font(font, True), resolve_emoji_font(emoji_font), pack or {}, size
        )
        self.outline = outline

    def measure(self, text: str) -> int:
        return self.factory.measure(text, self.outline)[0] if text else 0

    def height(self, text: str, width: int | None = None) -> int:
        w, h = self.factory.measure(text, self.outline)
        return max(1, round(h * min(1, width / w))) if width is not None else h

    def units(self, text: str):
        for kind, value in tokenize(text, self.factory.pack):
            if kind == "png":
                # Keep recognized shortcodes intact while wrapping.
                name = next(
                    name for name, path in self.factory.pack.items() if path == value
                )
                yield "[" + name + "]"
            elif kind == "emoji":
                cluster = ""
                for char in value:
                    if (
                        cluster
                        and ord(char) not in _STRIP
                        and not cluster.endswith("\u200d")
                    ):
                        yield cluster
                        cluster = ""
                    cluster += char
                if cluster:
                    yield cluster
            else:
                yield from value

    def wrap(self, text: str, budget_px: int) -> list[str]:
        """Wrap words/kinsoku clusters; indivisible overwide groups fit on draw."""
        if budget_px <= 0:
            raise ValueError("panel text width must be positive")
        opening = set("（([{【《〈「『〔〖〘〚")
        closing = set("。，、！？；：」』）》】〕〉〗〙〛）)]},.!?;:")

        def ascii_word(unit):
            return len(unit) == 1 and unit.isascii() and (unit.isalnum() or unit == "_")

        def atoms(paragraph):
            units = list(self.units(paragraph))
            tokens = []
            word = ""
            for index, unit in enumerate(units):
                following = units[index + 1] if index + 1 < len(units) else ""
                connector = unit in (".", "'", "’", "-", "/") or (
                    unit == "," and word[-1:].isdigit()
                )
                if ascii_word(unit) or (word and connector and ascii_word(following)):
                    word += unit
                    continue
                if word:
                    tokens.append(word)
                    word = ""
                tokens.append(unit)
            if word:
                tokens.append(word)
            # Only a word wider than the whole line may break internally.
            for token in tokens:
                if token and ascii_word(token[0]) and self.measure(token) > budget_px:
                    fragment = ""
                    for char in token:
                        if fragment and self.measure(fragment + char) > budget_px:
                            yield fragment
                            fragment = ""
                        fragment += char
                    if fragment:
                        yield fragment
                else:
                    yield token

        lines = []
        for paragraph in str(text).split("\n"):
            paragraph_start = len(lines)
            groups = []
            prefix = ""
            for atom in atoms(paragraph):
                if atom in opening or (prefix and atom.isspace()):
                    prefix += atom
                elif atom in closing:
                    if prefix:
                        groups.append(prefix + atom)
                        prefix = ""
                    else:
                        spaces = ""
                        while groups and groups[-1].isspace():
                            spaces = groups.pop() + spaces
                        if groups:
                            groups[-1] += spaces + atom
                        else:
                            groups.append(spaces + atom)
                else:
                    groups.append(prefix + atom)
                    prefix = ""
            if prefix:
                if groups:
                    groups[-1] += prefix
                else:
                    groups.append(prefix)
            current = ""
            for group in groups:
                if group.isspace() and not current:
                    continue
                if current and self.measure(current + group) > budget_px:
                    lines.append(current.rstrip())
                    current = ""
                if group.isspace() and not current:
                    continue
                current += group
            if current or len(lines) == paragraph_start:
                lines.append(current.rstrip())
        return lines

    def truncate(self, text: str, budget_px: int, ellipsis="...") -> str:
        if self.measure(text) <= budget_px:
            return text
        if self.measure(ellipsis) > budget_px:
            return ""
        result = ""
        for unit in self.units(text):
            if self.measure(result + unit + ellipsis) > budget_px:
                break
            result += unit
        return result + ellipsis


def parse_color(value: str, default: str = "FFFFFF") -> tuple[int, int, int]:
    """Parse a CSS-style RGB color."""
    text = str(value or default).lstrip("#")
    if len(text) != 6:
        text = default
    return tuple(int(text[index : index + 2], 16) for index in (0, 2, 4))


def materialize_emoticons(visual: dict[str, Any], store: Any) -> None:
    """Resolve URL-only platform emoticons into the shared local cache."""
    assets = list((visual.get("images") or {}).values())
    for node in visual.get("nodes", []):
        assets.extend((node.get("images") or {}).values())
    for asset in assets:
        if not isinstance(asset, dict) or asset.get("path") or not asset.get("url"):
            continue
        path = store.resolve(asset)
        if path:
            asset["path"] = path


def round_mask(size, radius, shape, supersample=SUPERSAMPLE):
    """Standard circular corners, shared by panels and VIP pills."""
    if shape not in ("round_top", "round_bottom", "stadium", "round"):
        raise ValueError(f"unsupported round shape: {shape}")
    width, height = size
    if shape == "stadium":
        radius = min(width, height) / 2
    else:
        # A half-panel can have a radius larger than half its own height.
        radius = min(
            float(radius), width / 2, height if shape != "round" else height / 2
        )
    scale = supersample
    mask = Image.new("L", (width * scale, height * scale), 0)
    draw = ImageDraw.Draw(mask)
    r = max(0, radius * scale)
    w, h = mask.size
    if r == 0:
        draw.rectangle((0, 0, w - 1, h - 1), fill=255)
        return mask
    # Extend the unused half instead of Pillow's radius clamp at h/2.
    if shape == "round_top":
        draw.rounded_rectangle((0, 0, w - 1, h + 2 * r - 1), radius=r, fill=255)
    elif shape == "round_bottom":
        draw.rounded_rectangle((0, -2 * r, w - 1, h - 1), radius=r, fill=255)
    else:
        draw.rounded_rectangle((0, 0, w - 1, h - 1), radius=r, fill=255)
    return mask


def draw_round(size, radius, shape, fill=(0, 0, 0, 0), outline=(0, 0, 0, 0), border=0):
    """Draw shape and border at 4x, then downsample with LANCZOS."""
    scale = SUPERSAMPLE
    mask = round_mask(size, radius, shape, scale)
    image = Image.new("RGBA", mask.size, fill)
    image.putalpha(ImageChops.multiply(mask, Image.new("L", mask.size, fill[3])))
    if border > 0:
        padded = Image.new("L", (mask.width + 2 * scale, mask.height + 2 * scale), 0)
        padded.paste(mask, (scale, scale))
        inner = padded.filter(
            ImageFilter.MinFilter(2 * max(1, round(border * scale)) + 1)
        )
        inner = inner.crop((scale, scale, scale + mask.width, scale + mask.height))
        edge = ImageChops.subtract(mask, inner)
        layer = Image.new("RGBA", image.size, outline)
        layer.putalpha(
            ImageChops.multiply(edge, Image.new("L", image.size, outline[3]))
        )
        image.alpha_composite(layer)
    return image.resize(size, Image.Resampling.LANCZOS)


def draw_panel(
    visual: dict[str, Any], width: int, height: int, pack=None, emoji_font=None
) -> Image.Image:
    """Draw a panel from configured nodes; outlines form one outer contour."""
    scale = SUPERSAMPLE
    image = Image.new("RGBA", (width * scale, height * scale))
    panel_mask = Image.new("L", image.size, 0)
    border = 0
    outline = (0, 0, 0, 0)
    nodes = sorted(visual["nodes"], key=lambda node: node["layer"])
    for node in nodes:
        if node["type"] != "rect":
            continue
        x, y, w, h = (int(node[key]) for key in ("x", "y", "width", "height"))
        mask = round_mask((w, h), node["radius"], node["shape"], scale)
        fill = parse_color(node["color"]) + (round(255 * node["opacity"]),)
        layer = Image.new("RGBA", mask.size, fill)
        layer.putalpha(ImageChops.multiply(mask, Image.new("L", mask.size, fill[3])))
        image.alpha_composite(layer, (x * scale, y * scale))
        combined = Image.new("L", image.size, 0)
        combined.paste(mask, (x * scale, y * scale))
        panel_mask = ImageChops.lighter(panel_mask, combined)
        border = max(border, node["outline"])
        outline = parse_color(node["outline_color"]) + (
            round(255 * node["outline_opacity"]),
        )
    if border:
        padding = max(1, round(border * scale))
        expanded = Image.new(
            "L", (image.width + 2 * padding, image.height + 2 * padding), 0
        )
        expanded.paste(panel_mask, (padding, padding))
        inner = expanded.filter(ImageFilter.MinFilter(2 * padding + 1))
        inner = inner.crop(
            (padding, padding, padding + image.width, padding + image.height)
        )
        edge = ImageChops.subtract(panel_mask, inner)
        layer = Image.new("RGBA", image.size, outline)
        layer.putalpha(
            ImageChops.multiply(edge, Image.new("L", image.size, outline[3]))
        )
        image.alpha_composite(layer)
    image = image.resize((width, height), Image.Resampling.LANCZOS)
    for node in nodes:
        if node["type"] != "text":
            continue
        images = dict(pack or {})
        for token, asset in (node.get("images") or {}).items():
            name = token.strip("[]").lower()
            if asset.get("path"):
                images[name] = asset["path"]
            else:
                images.pop(name, None)
        text = PillowText(
            node["font"],
            node["size"],
            node["outline"],
            images,
            emoji_font or visual.get("emoji_font"),
        )
        factory = text.factory
        for index, line in enumerate(str(node["text"]).split("\n") or [""]):
            sprite = factory.get_sprite_image(
                line,
                parse_color(node["color"]),
                event_pack=images or None,
                outline=node["outline"],
                outline_rgb=parse_color(node["outline_color"]),
                fit_width=node.get("width"),
            )
            sprite.putalpha(
                sprite.getchannel("A").point(
                    lambda value: round(value * node["opacity"])
                )
            )
            x = int(node["x"]) - (sprite.width if node["anchor"] == "top_right" else 0)
            image.alpha_composite(
                sprite,
                (x, int(node["y"] + index * node.get("line_height", node["size"]))),
            )
    return image


def rgba_to_yuv(
    image: Image.Image,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Convert RGBA pixels to BT.601 NV12-compatible planes."""
    values = np.asarray(image, dtype=np.float32)
    red = values[:, :, 0]
    green = values[:, :, 1]
    blue = values[:, :, 2]
    alpha = values[:, :, 3]
    y = np.clip(16 + 0.1826 * red + 0.6142 * green + 0.0620 * blue, 0, 255)
    u = np.clip(128 - 0.1006 * red - 0.3386 * green + 0.4392 * blue, 0, 255)
    v = np.clip(128 + 0.4392 * red - 0.3989 * green - 0.0403 * blue, 0, 255)
    return tuple(np.rint(plane).astype(np.uint8).reshape(-1) for plane in (y, u, v)) + (
        np.rint(alpha).astype(np.uint8).reshape(-1),
    )


def detect_platform(source_path):
    """Prefer structured metadata; retain ASS/legacy URL detection as fallback."""
    try:
        path = pathlib.Path(source_path)
        with path.open(encoding="utf-8-sig") as stream:
            metadata = {}
            try:
                if path.suffix.lower() == ".json":
                    metadata = json.load(stream)
                elif path.suffix.lower() == ".jsonl":
                    metadata = json.loads(next(line for line in stream if line.strip()))
            except (ValueError, StopIteration) as error:
                log.debug("平台头不可解析，使用旧格式匹配 (%s): %s", path, error)
            if (
                isinstance(metadata, dict)
                and isinstance(metadata.get("platform"), str)
                and metadata["platform"].strip()
            ):
                return metadata["platform"].strip().lower()
            stream.seek(0)
            head = stream.read(2000)
        if '"platform":"bilibili"' in head or '"platform": "bilibili"' in head:
            return "bilibili"
        if '"platform":"douyin"' in head or '"platform": "douyin"' in head:
            return "douyin"
        if '"platform":"douyu"' in head or '"platform": "douyu"' in head:
            return "douyu"
        if '"platform":"huya"' in head or '"platform": "huya"' in head:
            return "huya"
        if "bilibili.com" in head:
            return "bilibili"
        if "douyin.com" in head:
            return "douyin"
        if "douyu.com" in head:
            return "douyu"
        if "huya.com" in head:
            return "huya"
    except (OSError, ValueError, TypeError, StopIteration) as error:
        log.debug("读取平台信息失败 (%s): %s", source_path, error)
    return ""


def build_atlas(
    plan_path,
    platform="bilibili",
    emoticon_dir=None,
    stop_check=None,
    pack_dir=None,
    emoji_font=None,
):
    """Build cached GPU planes from a RenderPlan in the task directory."""
    plan = (
        plan_path
        if isinstance(plan_path, dict)
        else json.loads(pathlib.Path(plan_path).read_text(encoding="utf-8"))
    )
    emoticon_dir = emoticon_dir or pathlib.Path("emoji_pack") / platform / "emoticon"
    emoticon_store = EmoticonStore(str(emoticon_dir))
    emoji_name = resolve_emoji_font(emoji_font)
    platform_pack = load_pack(pack_dir or str(pathlib.Path("emoji_pack") / platform))
    factories: dict[tuple[str, str, float], SpriteFactory] = {}
    atlas: list[list[int]] = [[], [], [], []]
    metadata = []
    for event in plan.get("events", []):
        if stop_check:
            stop_check()
        visual = event.get("visual") or {}
        materialize_emoticons(visual, emoticon_store)
        bounds = event.get("bounds") or {}
        width = max(1, int(bounds.get("width", 1)))
        height = max(1, int(bounds.get("height", 1)))
        style = visual.get("style") or {}
        image_pack = {}
        for token, asset in (visual.get("images") or {}).items():
            key = str(token).strip("[]").lower()
            if asset.get("path"):
                image_pack[key] = asset["path"]
        if visual.get("type") == "panel":
            image = draw_panel(visual, width, height, platform_pack, emoji_font)
            arrays = rgba_to_yuv(image)
        else:
            rgb = parse_color(style["color"])
            family = str(style["font"])
            # Existing RenderPlan line styles omit ``bold``; absence means the
            # regular face, while all required visual fields remain strict.
            bold = bool(style.get("bold", False))
            size = float(style["size"])
            font_name = resolve_font(family, bold)
            factory_key = (font_name, emoji_name, size)
            if factory_key not in factories:
                factories[factory_key] = SpriteFactory(
                    font_name, emoji_name, platform_pack, size
                )
            sprite = factories[factory_key].get_sprite(
                visual.get("text", ""),
                rgb,
                pill=(
                    {
                        "color": list(
                            parse_color(visual["pill"].get("color", "#FFC0CB"))
                        ),
                        "pad": int(
                            visual["pill"].get("padding", visual["pill"].get("pad", 6))
                        ),
                        "border": int(visual["pill"].get("border", 1)),
                    }
                    if visual.get("pill")
                    else None
                ),
                event_pack=image_pack if visual.get("images") else None,
                outline=style["outline"],
                outline_rgb=parse_color(style["outline_color"]),
                scroll_vertical_gap=float(visual.get("scroll_vertical_gap", 0)),
            )
            opacity = float(style["opacity"])
            arrays = (
                sprite[0],
                sprite[1],
                sprite[2],
                np.rint(sprite[3] * opacity * 255).astype(np.uint8),
            )
            width = int(sprite[4])
            height = int(sprite[5])
            arrays = tuple(
                np.rint(array).astype(np.uint8).reshape(-1) for array in arrays
            )
        offset = len(atlas[0])
        for destination, source in zip(atlas, arrays):
            destination.extend(source.tolist())
        path = event.get("path") or []
        metadata.append(
            {
                "id": event.get("id"),
                "track": event_track(event),
                "offset": offset,
                "width": width,
                "height": height,
                "start": float(path[0][0]) if path else 0.0,
                "end": float(path[-1][0]) if path else 0.0,
                "path": path,
                "motion": visual.get("motion") or {},
            }
        )
    emoticon_store.save()
    return {
        "duration": plan["duration"],
        "events": metadata,
        **{
            key: np.asarray(values, dtype=np.uint8)
            for key, values in zip(("y", "u", "v", "a"), atlas)
        },
        "atlas_bytes": sum(len(values) for values in atlas),
    }
