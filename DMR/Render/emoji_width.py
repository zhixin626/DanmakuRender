import os
import re

from PIL import Image, ImageFont

from .emoji_render_mt import (
    SCALE,
    _STRIP,
    is_emoji,
    load_pack,
    resolve_emoji_font,
    resolve_font,
)


SHORTCODE_RE = re.compile(r"\[([^\[\]\r\n]{1,200})\]")


class EmojiWidthMeasurer:
    """按 emoji_render_mt 的精灵生成规则计算 R2L 内容宽度。"""

    def __init__(
        self,
        font: str,
        fontsize: int,
        platform: str,
        emoji_pack: str = None,
        emoji_font: str = None,
    ):
        self.fontsize = int(fontsize)
        self.pixel_size = max(1, int(round(self.fontsize * SCALE)))
        self.text_font = ImageFont.truetype(resolve_font(font, True), self.pixel_size)
        self.emoji_font = self._load_emoji_font(resolve_emoji_font(emoji_font))
        self.emoji_scale = self.pixel_size / max(
            1, getattr(self.emoji_font, "size", self.pixel_size)
        )
        if emoji_pack:
            pack_path = emoji_pack
        else:
            pack_path = os.path.join("./emoji_pack", platform) if platform else "./emoji_pack"
        self.pack = load_pack(pack_path)
        self.png_height = max(1, int(round(self.pixel_size * 1.25)))
        self.text_ascent, self.text_descent = self.text_font.getmetrics()
        emoji_ascent, emoji_descent = self.emoji_font.getmetrics()
        self.emoji_ascent = int(round(emoji_ascent * self.emoji_scale))
        self.emoji_descent = int(round(emoji_descent * self.emoji_scale))
        self._image_widths = {}
        self._cache = {}

    def is_special(self, text: str, emoticon: dict = None) -> bool:
        if emoticon:
            return True
        if any(is_emoji(char) for char in (text or "")):
            return True
        return any(
            match.group(1).lower() in self.pack
            for match in SHORTCODE_RE.finditer(text or "")
        )

    def vertical_metrics(self, text: str, emoticon: dict = None) -> tuple[int, int]:
        """返回相对共享文字基线的 ``(ascent, descent)``。

        计算规则与 emoji_render_mt.get_sprite 完全相同。轨道 y 始终是 ASS
        ``\\an7`` 的文字行顶；图片或 Emoji 只扩展视觉盒，不改变文字基线。
        """
        text = text or ""
        has_emoji = any(is_emoji(char) for char in text)
        has_png = bool(emoticon) or any(
            match.group(1).lower() in self.pack
            for match in SHORTCODE_RE.finditer(text)
        )
        png_center = -int(round(self.text_ascent * 0.38))
        png_top = png_center - self.png_height // 2
        png_bottom = png_center + (self.png_height - self.png_height // 2)
        ascent = max(
            self.text_ascent,
            -png_top if has_png else 0,
            self.emoji_ascent if has_emoji else 0,
        )
        descent = max(
            self.text_descent,
            png_bottom if has_png else 0,
            self.emoji_descent if has_emoji else 0,
        )
        return int(ascent), int(descent)

    def _load_emoji_font(self, path):
        try:
            return ImageFont.truetype(path, self.pixel_size)
        except OSError:
            for size in (109, 128, 136, 96, 160, 64, 32):
                try:
                    return ImageFont.truetype(path, size)
                except OSError:
                    continue
        return self.text_font

    def _png_width(self, path: str) -> int:
        cached = self._image_widths.get(path)
        if cached is not None:
            return cached
        try:
            with Image.open(path) as image:
                width = max(1, int(round(image.width * self.png_height / image.height)))
        except Exception:
            width = self.png_height
        self._image_widths[path] = width
        return width

    def _emoticon_width(self, meta: dict) -> int:
        try:
            width = float(meta.get("width"))
            height = float(meta.get("height"))
            if width > 0 and height > 0:
                return max(1, int(round(width * self.png_height / height)))
        except (TypeError, ValueError):
            pass
        return self.png_height

    def resolve_images(self, text: str, emoticon: dict = None) -> dict:
        """Return the exact shortcode assets used by the raster renderer.

        Keys keep their bracket form so the RenderPlan stays readable.  ``size``
        is the intended display size, not the source bitmap dimensions.
        """
        result = {}
        exact_name = None
        if emoticon:
            exact_name = str(emoticon.get("name") or "").strip()
            if exact_name.startswith("[") and exact_name.endswith("]"):
                exact_name = exact_name[1:-1]
            if exact_name:
                asset = {
                    key: emoticon[key]
                    for key in (
                        "emoticon_unique",
                        "name",
                        "url",
                        "width",
                        "height",
                        "is_dynamic",
                    )
                    if emoticon.get(key) is not None
                }
                asset["size"] = [self._emoticon_width(emoticon), self.png_height]
                result[f"[{exact_name}]"] = asset

        # A platform emoticon event deliberately uses only its exact image.  This
        # mirrors emoji_render_mt's current event-specific pack behaviour.
        if emoticon:
            return result

        for match in SHORTCODE_RE.finditer(text or ""):
            name = match.group(1)
            path = self.pack.get(name.lower())
            if not path:
                continue
            result.setdefault(
                f"[{name}]",
                {
                    "path": path,
                    "size": [self._png_width(path), self.png_height],
                },
            )
        return result

    def measure(self, text: str, emoticon: dict = None) -> int:
        text = (text or "").translate(_STRIP)
        emoticon_key = None
        emoticon_name = None
        emoticon_width = None
        if emoticon:
            emoticon_key = (
                emoticon.get("emoticon_unique"),
                emoticon.get("name"),
                emoticon.get("width"),
                emoticon.get("height"),
            )
            emoticon_name = str(emoticon.get("name") or "").strip()
            if emoticon_name.startswith("[") and emoticon_name.endswith("]"):
                emoticon_name = emoticon_name[1:-1]
            emoticon_name = emoticon_name.lower()
            emoticon_width = self._emoticon_width(emoticon)
        key = (text, emoticon_key)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        width = 0
        parts = SHORTCODE_RE.split(text)
        for index, part in enumerate(parts):
            if index % 2 == 1:
                shortcode = part.lower()
                # 平台纯表情事件只把与 emoticon.name 匹配的短码替换成该事件
                # 的图片；VIP/模板增加的其他文字仍须逐段测量。
                if (
                    emoticon_width is not None
                    and emoticon_name
                    and shortcode == emoticon_name
                ):
                    width += emoticon_width
                    continue

                # 普通内联表情没有 emoticon 元数据，按本地 emoji_pack 的
                # 实际图片尺寸测量。纯表情事件要与渲染器保持一致，不混用
                # 默认 pack，只绑定该事件指定的图片。
                if emoticon is None:
                    image_path = self.pack.get(shortcode)
                    if image_path:
                        width += self._png_width(image_path)
                        continue

                part = f"[{part}]"
            current = ""
            current_is_emoji = None
            for char in part:
                char_is_emoji = bool(is_emoji(char))
                if current and char_is_emoji != current_is_emoji:
                    width += self._measure_run(current, current_is_emoji)
                    current = ""
                current += char
                current_is_emoji = char_is_emoji
            if current:
                width += self._measure_run(current, current_is_emoji)

        result = max(0, int(width))
        self._cache[key] = result
        return result

    def _measure_run(self, text: str, emoji: bool) -> int:
        if emoji:
            return int(self.emoji_font.getlength(text) * self.emoji_scale)
        return int(self.text_font.getlength(text))
