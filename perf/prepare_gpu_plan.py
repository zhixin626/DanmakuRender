"""Build a compact YUV sprite atlas from a RenderPlan."""

from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import sys
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SPRITES_PATH = ROOT / "DMR" / "Render" / "sprites.py"
SPRITES_SPEC = importlib.util.spec_from_file_location("dmr_shared_sprites", SPRITES_PATH)
if SPRITES_SPEC is None or SPRITES_SPEC.loader is None:
    raise ImportError(f"unable to load {SPRITES_PATH}")
SPRITES_MODULE = importlib.util.module_from_spec(SPRITES_SPEC)
SPRITES_SPEC.loader.exec_module(SPRITES_MODULE)
SpriteFactory = SPRITES_MODULE.SpriteFactory
resolve_emoji_font = SPRITES_MODULE.resolve_emoji_font
resolve_font = SPRITES_MODULE.resolve_font


def parse_color(value: str, default: str = "FFFFFF") -> tuple[int, int, int]:
    """Parse a CSS-style RGB color."""
    text = str(value or default).lstrip("#")
    if len(text) != 6:
        text = default
    return tuple(int(text[index:index + 2], 16) for index in (0, 2, 4))


def panel_sprite(visual: dict[str, Any], width: int, height: int) -> Image.Image:
    """Render panel nodes with configured fills, borders and text outlines."""
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    for node in sorted(visual.get("nodes", []), key=lambda item: item.get("layer", 0)):
        if node.get("type") == "rect":
            x = int(node.get("x", 0))
            y = int(node.get("y", 0))
            w = int(node.get("width", width))
            h = int(node.get("height", height))
            alpha = int(255 * float(node.get("opacity", 1.0)))
            fill = parse_color(node.get("color", "#FFFFFF")) + (alpha,)
            outline = parse_color(node.get("outline_color", "#000000"))
            outline_alpha = int(255 * float(node.get("outline_opacity", 1.0)))
            draw.rounded_rectangle(
                (x, y, x + w, y + h),
                radius=int(node.get("radius", 0)),
                fill=fill,
                outline=outline + (outline_alpha,),
                width=max(0, int(node.get("outline", 0))),
            )
        if node.get("type") == "text":
            family = str(node.get("font", "Microsoft YaHei"))
            font_path = resolve_font(family, True)
            font = ImageFont.truetype(font_path, int(node.get("size", 30)))
            fill = parse_color(node.get("color", "#FFFFFF")) + (255,)
            x = int(node.get("x", 0))
            y = int(node.get("y", 0))
            anchor = "ra" if node.get("anchor") == "top_right" else "la"
            draw.text(
                (x, y),
                node.get("text", ""),
                font=font,
                fill=fill,
                anchor=anchor,
                stroke_width=max(0, int(node.get("outline", 0))),
                stroke_fill=parse_color(node.get("outline_color", "#000000")) + (255,),
            )
    return image


def rgba_to_yuv(image: Image.Image) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Convert RGBA pixels to BT.601 NV12-compatible planes."""
    values = np.asarray(image, dtype=np.float32)
    red = values[:, :, 0]
    green = values[:, :, 1]
    blue = values[:, :, 2]
    alpha = values[:, :, 3]
    y = np.clip(16 + 0.1826 * red + 0.6142 * green + 0.0620 * blue, 0, 255)
    u = np.clip(128 - 0.1006 * red - 0.3386 * green + 0.4392 * blue, 0, 255)
    v = np.clip(128 + 0.4392 * red - 0.3989 * green - 0.0403 * blue, 0, 255)
    return tuple(np.rint(plane).astype(np.uint8).reshape(-1) for plane in (y, u, v)) + (np.rint(alpha).astype(np.uint8).reshape(-1),)


def main() -> None:
    """Build atlas arrays and event path metadata for gpu_render."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    args.output.mkdir(parents=True, exist_ok=True)
    emoji_name = resolve_emoji_font(None)
    factories: dict[tuple[str, str, float], SpriteFactory] = {}
    atlas: list[list[int]] = [[], [], [], []]
    metadata = []
    for event in plan.get("events", []):
        visual = event.get("visual") or {}
        bounds = event.get("bounds") or {}
        width = max(1, int(bounds.get("width", 1)))
        height = max(1, int(bounds.get("height", 1)))
        style = visual.get("style") or {}
        rgb = parse_color(style.get("color", "#FFFFFF"))
        image_pack = {}
        for token, asset in (visual.get("images") or {}).items():
            key = str(token).strip("[]").lower()
            if asset.get("path"):
                image_pack[key] = asset["path"]
        if visual.get("type") == "panel":
            image = panel_sprite(visual, width, height)
            arrays = rgba_to_yuv(image)
        else:
            family = str(style.get("font", "Microsoft YaHei"))
            bold = bool(style.get("bold", True))
            size = float(style.get("size", 60))
            font_name = resolve_font(family, bold)
            factory_key = (font_name, emoji_name, size)
            if factory_key not in factories:
                factories[factory_key] = SpriteFactory(font_name, emoji_name, {}, size)
            sprite = factories[factory_key].get_sprite(
                visual.get("text", ""),
                rgb,
                pill=(
                    {
                        "color": list(parse_color(visual["pill"].get("color", "#FFC0CB"))),
                        "pad": int(visual["pill"].get("padding", visual["pill"].get("pad", 6))),
                        "border": int(visual["pill"].get("border", 1)),
                    }
                    if visual.get("pill")
                    else None
                ),
                event_pack=image_pack or None,
                outline=style.get("outline", 1),
                outline_rgb=parse_color(style.get("outline_color", "#000000")),
            )
            opacity = float(style.get("opacity", 1.0))
            arrays = (sprite[0], sprite[1], sprite[2], np.rint(sprite[3] * opacity * 255).astype(np.uint8))
            width = int(sprite[4])
            height = int(sprite[5])
            arrays = tuple(np.rint(array).astype(np.uint8).reshape(-1) for array in arrays)
        offset = len(atlas[0])
        for destination, source in zip(atlas, arrays):
            destination.extend(source.tolist())
        path = event.get("path") or []
        metadata.append({
            "id": event.get("id"),
            "offset": offset,
            "width": width,
            "height": height,
            "start": float(path[0][0]) if path else 0.0,
            "end": float(path[-1][0]) if path else 0.0,
            "path": path,
            "motion": visual.get("motion") or {},
        })
    np.savez_compressed(
        args.output / "sprites.npz",
        y=np.asarray(atlas[0], dtype=np.uint8),
        u=np.asarray(atlas[1], dtype=np.uint8),
        v=np.asarray(atlas[2], dtype=np.uint8),
        a=np.asarray(atlas[3], dtype=np.uint8),
    )
    (args.output / "events.json").write_text(
        json.dumps({"duration": plan.get("duration"), "events": metadata}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({"events": len(metadata), "atlas_bytes": len(atlas[0])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
