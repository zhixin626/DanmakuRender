import json
import sys

sys.path.append("D:/zhixin_videos")
from manim_imports_custom import *


FONT = "WenYue XinQingNianTi (Authorization Required)"


def edge_from_arg(value, default=ORIGIN):
    try:
        return {-1: LEFT, 0: ORIGIN, 1: RIGHT}[int(value)]
    except (KeyError, TypeError, ValueError):
        return default


def add_background(scene, image_path, opacity):
    if image_path:
        image = ImageMobject(image_path)
        image.set_opacity(opacity)
        image.set_height(FRAME_HEIGHT)
        scene.add(image)


def create_lines(texts, colors):
    return VGroup(*[
        Text(text, font=FONT).set_color(color).set_backstroke(BLACK, 6)
        for text, color in zip(texts, colors)
    ])


def align_to_safe(lines, safe_rec, edge):
    if np.array_equal(edge, LEFT):
        shift = safe_rec.get_left()[0] - lines.get_left()[0]
    elif np.array_equal(edge, RIGHT):
        shift = safe_rec.get_right()[0] - lines.get_right()[0]
    else:
        shift = safe_rec.get_center()[0] - lines.get_center()[0]
    lines.shift(RIGHT * shift)


def layout_lines(lines, safe_rec, edge):
    lines.arrange(DOWN, aligned_edge=edge)
    lines.set_width(safe_rec.get_width() - 1)
    max_height = safe_rec.get_height() - 1
    if lines.get_height() > max_height:
        lines.set_height(max_height)
    lines.move_to(safe_rec)
    align_to_safe(lines, safe_rec, edge)


class cover(InteractiveScene):
    default_camera_config = {"background_color": BLACK}

    def construct(self):
        # start
        if len(sys.argv) >= 7:
            texts = json.loads(sys.argv[3])
            colors = json.loads(sys.argv[4])
            image_path = sys.argv[5]
            frame_opacity = float(sys.argv[6])
            edge = edge_from_arg(sys.argv[7] if len(sys.argv) > 7 else None)
        else:
            texts = ["冷狗", "vs", "不可一世"]
            colors = ["#FFFFFF", "#FFFF00", "#FFFFFF"]
            image_path = None
            frame_opacity = 0.2
            edge = LEFT

        add_background(self, image_path, frame_opacity)
        safe_rec = Safe(y=-2.5).rec
        lines = create_lines(texts, colors)
        layout_lines(lines, safe_rec, edge)
        self.add(lines)
