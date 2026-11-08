from __future__ import annotations

import json
import math
import os


SLIDE_BACKGROUND_LAYER = 0
SLIDE_TEXT_LAYER = 1
R2L_BACKGROUND_LAYER = 10
R2L_TEXT_LAYER = 11


def _ass_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    hour = int(seconds // 3600)
    minute = int((seconds % 3600) // 60)
    second = seconds % 60
    return f"{hour}:{minute:02d}:{second:05.2f}"


def _ass_text(value) -> str:
    return (
        str(value or "")
        .replace("{", r"\{")
        .replace("}", r"\}")
        .replace("\r\n", r"\N")
        .replace("\n", r"\N")
        .replace("\r", r"\N")
    )


def _bgr(value, default="FFFFFF") -> str:
    raw = str(value or default).lstrip("#").upper()
    if len(raw) != 6:
        raw = default
    return raw[4:6] + raw[2:4] + raw[0:2]


def _alpha(opacity) -> str:
    try:
        value = min(1.0, max(0.0, float(opacity)))
    except (TypeError, ValueError):
        value = 1.0
    return f"{255 - int(round(value * 255)):02X}"


def _position_tag(a: list, b: list, ox=0.0, oy=0.0) -> str:
    x0, y0 = float(a[1]) + ox, float(a[2]) + oy
    x1, y1 = float(b[1]) + ox, float(b[2]) + oy
    if math.isclose(x0, x1, abs_tol=0.01) and math.isclose(y0, y1, abs_tol=0.01):
        return f"\\pos({x1:.2f},{y1:.2f})"
    return f"\\move({x0:.2f},{y0:.2f},{x1:.2f},{y1:.2f})"


def _panel_shape(node: dict) -> str:
    width = float(node.get("width", 0) or 0)
    height = float(node.get("height", 0) or 0)
    radius = max(0.0, float(node.get("radius", 0) or 0))
    shape = node.get("shape")
    if shape == "round_top":
        radius = min(radius, height, width / 2)
        return (
            f"m {radius:g} 0 l {width - radius:g} 0 "
            f"b {width:g} 0 {width:g} 0 {width:g} {radius:g} "
            f"l {width:g} {height:g} l 0 {height:g} l 0 {radius:g} "
            f"b 0 0 0 0 {radius:g} 0"
        )
    if shape == "round_bottom":
        radius = min(radius, height, width / 2)
        return (
            f"m 0 0 l {width:g} 0 l {width:g} {height - radius:g} "
            f"b {width:g} {height:g} {width:g} {height:g} "
            f"{width - radius:g} {height:g} l {radius:g} {height:g} "
            f"b 0 {height:g} 0 {height:g} 0 {height - radius:g} l 0 0"
        )
    return f"m 0 0 l {width:g} 0 l {width:g} {height:g} l 0 {height:g}"


def _panel_phase(first: list, second: list, motion: dict) -> str:
    x0, x1 = float(first[1]), float(second[1])
    y0, y1 = float(first[2]), float(second[2])
    if math.isclose(x0, x1, abs_tol=0.01):
        return "static" if math.isclose(y0, y1, abs_tol=0.01) else "push"
    try:
        inside_x = float(motion["inside_x"])
        outside_x = float(motion["outside_x"])
        before = abs(x0 - inside_x) / max(abs(outside_x - inside_x), 1e-7)
        after = abs(x1 - inside_x) / max(abs(outside_x - inside_x), 1e-7)
        return "entry" if after < before else "exit"
    except (KeyError, TypeError, ValueError):
        return "entry"


def _panel_fade(
    phase: str,
    duration: float,
    first: list,
    second: list,
    motion: dict,
) -> str:
    # Entry/exit dialogues are already cut to the configured slide duration,
    # so their fade should cover that exact interval as well.  A segment may
    # begin/end in the middle of the movement; use x to continue from the
    # corresponding intermediate alpha instead of restarting a full fade.
    milliseconds = max(1, int(round(duration * 1000)))
    if phase in ("entry", "exit"):
        try:
            inside_x = float(motion["inside_x"])
            outside_x = float(motion["outside_x"])
            distance = inside_x - outside_x
            if math.isclose(distance, 0.0, abs_tol=1e-7):
                raise ValueError("panel motion has zero distance")

            def fade_alpha(point):
                visible = (float(point[1]) - outside_x) / distance
                visible = min(1.0, max(0.0, visible))
                return int(round(255 * (1.0 - visible)))

            alpha0 = fade_alpha(first)
            alpha1 = fade_alpha(second)
            if phase == "entry" and alpha0 == 255 and alpha1 == 0:
                return f"\\fad({milliseconds},0)"
            if phase == "exit" and alpha0 == 0 and alpha1 == 255:
                return f"\\fad(0,{milliseconds})"
            return (
                f"\\fade({alpha0},{alpha1},{alpha1},"
                f"0,{milliseconds},{milliseconds},{milliseconds})"
            )
        except (KeyError, TypeError, ValueError):
            if phase == "entry":
                return f"\\fad({milliseconds},0)"
            return f"\\fad(0,{milliseconds})"
    return "\\fad(0,0)"


def _dialogue(t0, t1, layer, tags, body, style="Default") -> str:
    return (
        f"Dialogue: {int(layer)},{_ass_time(t0)},{_ass_time(t1)},"
        f"{style},,0,0,0,,{{{tags}}}{body}\n"
    )


def _pill_path(width: float, height: float) -> str:
    """ASS ``\\p1`` stadium path with semicircular left and right ends."""
    width = max(float(width), float(height))
    height = max(1.0, float(height))
    radius = height / 2.0
    kappa = 0.5522847498 * radius

    def point(x, y):
        return f"{round(x)} {round(y)}"

    top_left = (radius, 0.0)
    top_right = (width - radius, 0.0)
    right = (width, radius)
    bottom_right = (width - radius, height)
    bottom_left = (radius, height)
    left = (0.0, radius)
    return " ".join(
        [
            f"m {point(*top_left)}",
            f"l {point(*top_right)}",
            (
                f"b {point(top_right[0] + kappa, top_right[1])} "
                f"{point(right[0], right[1] - kappa)} {point(*right)}"
            ),
            (
                f"b {point(right[0], right[1] + kappa)} "
                f"{point(bottom_right[0] + kappa, bottom_right[1])} "
                f"{point(*bottom_right)}"
            ),
            f"l {point(*bottom_left)}",
            (
                f"b {point(bottom_left[0] - kappa, bottom_left[1])} "
                f"{point(left[0], left[1] + kappa)} {point(*left)}"
            ),
            (
                f"b {point(left[0], left[1] - kappa)} "
                f"{point(top_left[0] - kappa, top_left[1])} "
                f"{point(*top_left)}"
            ),
        ]
    )


def _line_dialogues(event: dict, include_raster_text: bool) -> list[str]:
    mode = event.get("mode", "ass")
    if mode == "raster" and not include_raster_text:
        return []
    visual = event.get("visual") or {}
    text = visual.get("text", event.get("text", ""))
    style = visual.get("style") or event.get("style") or {}
    pill = visual.get("pill") or event.get("pill")
    bounds = event.get("bounds") or {}
    width = float(bounds.get("width", 0) or 0)
    height = float(bounds.get("height", style.get("size", 36)) or 36)
    path = event.get("path") or []
    if len(path) < 2 or not text:
        return []

    font = str(style.get("font", "Microsoft YaHei")).replace(",", " ")
    size = float(style.get("size", 36) or 36)
    outline = float(style.get("outline", 1) or 0)
    color = _bgr(style.get("color", "#FFFFFF"))
    outline_color = _bgr(style.get("outline_color", "#000000"), "000000")
    alpha = _alpha(style.get("opacity", 0.8))
    text_tags = (
        f"\\an7\\fn{font}\\fs{size:g}\\b1"
        f"\\1c&H{color}&\\1a&H{alpha}&"
        f"\\bord{outline:g}\\3c&H{outline_color}&\\shad0"
    )
    result = []
    for first, second in zip(path, path[1:]):
        t0, t1 = float(first[0]), float(second[0])
        if t1 <= t0:
            continue
        if pill:
            pad = float(pill.get("padding", pill.get("pad", 0)) or 0)
            border = float(pill.get("border", 2) or 2)
            pill_color = _bgr(pill.get("color", "#FFC0CB"), "FFC0CB")
            pill_path = _pill_path(width, height)
            tags = (
                "\\an7\\p1"
                + _position_tag(first, second)
                + f"\\1a&HFF&\\3c&H{pill_color}&\\bord{border:g}\\shad0"
            )
            result.append(
                _dialogue(t0, t1, R2L_BACKGROUND_LAYER, tags, pill_path)
            )
            text_offset = max(0.0, pad)
        else:
            text_offset = 0.0
        tags = text_tags + _position_tag(first, second, text_offset, 0)
        result.append(
            _dialogue(
                t0,
                t1,
                R2L_TEXT_LAYER,
                tags,
                _ass_text(text),
                style="R2L",
            )
        )
    return result


def _panel_dialogues(event: dict) -> list[str]:
    visual = event.get("visual") or {}
    nodes = visual.get("nodes") or []
    path = event.get("path") or []
    if len(path) < 2:
        return []
    motion = visual.get("motion") or {}
    result = []
    for first, second in zip(path, path[1:]):
        t0, t1 = float(first[0]), float(second[0])
        if t1 <= t0:
            continue
        phase = _panel_phase(first, second, motion)
        fade = _panel_fade(phase, t1 - t0, first, second, motion)
        for node in nodes:
            node_type = node.get("type")
            ox = float(node.get("x", 0) or 0)
            oy = float(node.get("y", 0) or 0)
            default_layer = (
                SLIDE_BACKGROUND_LAYER
                if node_type == "rect"
                else SLIDE_TEXT_LAYER
            )
            layer = int(node.get("layer", default_layer))
            pos = _position_tag(first, second, ox, oy)
            if node_type == "rect":
                width = float(node.get("width", 0) or 0)
                height = float(node.get("height", 0) or 0)
                if width <= 0 or height <= 0:
                    continue
                body = _panel_shape(node)
                outline = float(node.get("outline", 0) or 0)
                tags = (
                    f"\\an7{fade}{pos}\\p1\\bord{outline:g}\\shad0"
                    f"\\1c&H{_bgr(node.get('color'))}&"
                    f"\\1a&H{_alpha(node.get('opacity', 1.0))}&"
                    f"\\3c&H{_bgr(node.get('outline_color', '#000000'), '000000')}&"
                    f"\\3a&H{_alpha(node.get('outline_opacity', 1.0))}&"
                )
                result.append(_dialogue(t0, t1, layer, tags, body, style="Panel"))
            elif node_type == "text":
                font = str(node.get("font", "Microsoft YaHei")).replace(",", " ")
                size = float(node.get("size", 30) or 30)
                alignment = 9 if node.get("anchor") == "top_right" else 7
                tags = (
                    f"\\an{alignment}{fade}{pos}\\fn{font}\\fs{size:g}\\b1\\shad0\\q2"
                    f"\\1c&H{_bgr(node.get('color'))}&"
                    f"\\1a&H{_alpha(node.get('opacity', 1.0))}&"
                    f"\\bord{float(node.get('outline', 0) or 0):g}"
                    f"\\3c&H{_bgr(node.get('outline_color', '#000000'), '000000')}&"
                )
                result.append(
                    _dialogue(t0, t1, layer, tags, _ass_text(node.get("text")), style="Panel")
                )
    return result


def render_plan_to_ass(
    plan: dict | str,
    output_path: str,
    *,
    include_raster_text: bool = False,
    width: int = 1920,
    height: int = 1080,
) -> str:
    if isinstance(plan, (str, os.PathLike)):
        with open(plan, "r", encoding="utf-8") as fp:
            plan = json.load(fp)
    if int(plan.get("schema", 0)) != 1:
        raise ValueError(f"unsupported RenderPlan schema: {plan.get('schema')}")

    default_style = {}
    for event in plan.get("events") or []:
        visual = event.get("visual") or {}
        if visual.get("type", "line") == "line":
            default_style = visual.get("style") or event.get("style") or {}
            break
    default_font = str(default_style.get("font", "Microsoft YaHei")).replace(",", " ")
    default_size = float(default_style.get("size", 36) or 36)
    default_outline = float(default_style.get("outline", 1) or 0)

    lines = [
        "[Script Info]",
        "Title: DMR RenderPlan",
        "ScriptType: v4.00+",
        "Collisions: Normal",
        f"PlayResX: {int(width)}",
        f"PlayResY: {int(height)}",
        "Timer: 100.0000",
        "WrapStyle: 2",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Default,Microsoft YaHei,36,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,1,0,7,0,0,0,1",
        f"Style: R2L,{default_font},{default_size:g},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,{default_outline:g},0,7,0,0,0,1",
        "Style: Panel,Microsoft YaHei,30,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    dialogues = []
    for event in plan.get("events") or []:
        visual = event.get("visual") or {}
        if visual.get("type", "line") == "panel":
            if event.get("mode", "ass") == "ass":
                dialogues.extend(_panel_dialogues(event))
        else:
            dialogues.extend(_line_dialogues(event, include_raster_text))

    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8", newline="\n") as fp:
        fp.write("\n".join(lines))
        fp.write("\n")
        fp.writelines(dialogues)
    return output_path
