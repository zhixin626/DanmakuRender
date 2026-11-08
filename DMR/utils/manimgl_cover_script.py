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


def create_texts(name_text, time_text, color, year_text):
    kwargs = {"font": FONT}
    name = Text(name_text, **kwargs).set_color(color).scale(1.8)
    time = Text(time_text, **kwargs).set_color(WHITE).scale(1.5)
    replay = Text("直播回放", **kwargs).set_color(WHITE)
    year = Text(year_text, font="Freestyle Script").scale(2.3).set_color(color)
    for mob in (name, time, replay, year):
        mob.set_backstroke(BLACK, 6)
    return name, time, replay, year


def fit_to_safe(group, safe_rec):
    group.set_width(safe_rec.get_width() - 1)
    max_height = safe_rec.get_height() - 1
    if group.get_height() > max_height:
        group.set_height(max_height)
    group.move_to(safe_rec)


def align_to_safe(overlay, safe_rec, edge):
    if np.array_equal(edge, LEFT):
        shift = safe_rec.get_left()[0] - overlay.get_left()[0]
    elif np.array_equal(edge, RIGHT):
        shift = safe_rec.get_right()[0] - overlay.get_right()[0]
    else:
        shift = safe_rec.get_center()[0] - overlay.get_center()[0]
    overlay.shift(RIGHT * shift)


def layout_texts(name, time, replay, year, color, safe_rec, edge):
    group = VGroup(name, time, replay).arrange(DOWN, aligned_edge=edge)
    fit_to_safe(group, safe_rec)
    name.shift(np.array([0, 3.4, 0]) - name.get_top())

    year_group = VGroup(year, Underline(year, stroke_color=color))
    year_group.next_to(time, RIGHT, aligned_edge=DOWN)
    overlay = VGroup(name, time, replay, year_group)
    align_to_safe(overlay, safe_rec, edge)
    return group, year_group


class cover(InteractiveScene):
    default_camera_config = {"background_color": BLACK}

    def construct(self):
        # start
        if len(sys.argv) >= 9:
            name = sys.argv[3]
            time = sys.argv[4]
            color = sys.argv[5]
            year = sys.argv[6]
            image_path = sys.argv[7]
            frame_opacity = float(sys.argv[8])
            edge = edge_from_arg(sys.argv[9] if len(sys.argv) > 9 else None)
        else:
            name = "月亮3"
            time = "6月7日"
            color = "#FFFF00"
            year = "2026"
            image_path = None
            frame_opacity = 0.2
            edge = LEFT

        add_background(self, image_path, frame_opacity)
        safe_rec = Safe(y=-2.5).rec
        mobs = create_texts(name, time, color, year)
        group, year_group = layout_texts(*mobs, color, safe_rec, edge)
        self.add(year_group, group)
