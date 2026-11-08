import json
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)
_MANIMGL_EXE = R"D:\manim\venv\Scripts\manimgl.exe"


def _edge_value(edge):
    try:
        edge = int(edge)
    except (TypeError, ValueError) as exc:
        raise ValueError("cover.edge 必须是 -1、0 或 1") from exc
    if edge not in (-1, 0, 1):
        raise ValueError("cover.edge 必须是 -1、0 或 1")
    return edge


def _render(script, scene_name, args, output_dir, is_open, extra_args, output_filename):
    cmd = [_MANIMGL_EXE, str(script), scene_name, *map(str, args), "-s"]
    cmd += ["-o" if is_open else "-w", "--video_dir", output_dir, "-q"]
    if extra_args:
        cmd += list(extra_args)
    subprocess.run(cmd, check=True)

    rendered_path = Path(output_dir) / "cover.png"
    if output_filename and output_filename != "cover.png":
        target_path = Path(output_dir) / output_filename
        rendered_path.replace(target_path)
        return str(target_path)
    return str(rendered_path)


def rendercover_with_manimgl(
    name,
    time,
    color,
    year,
    output_dir,
    image_path=None,
    frame_opacity=0.2,
    is_open=False,
    extra_args=None,
    output_filename=None,
    edge=0,
):
    args = [
        name, time, color, year, image_path or "", str(frame_opacity),
        _edge_value(edge),
    ]
    script = Path(__file__).with_name("manimgl_cover_script.py")
    return _render(
        script, "cover", args, output_dir, is_open, extra_args, output_filename
    )


def render_text_cover_with_manimgl(
    texts,
    colors,
    output_dir,
    image_path=None,
    frame_opacity=0.2,
    is_open=False,
    extra_args=None,
    output_filename=None,
    edge=0,
    transparent=False,
):
    """纵向排列一组文字，并缩放到模板的 Safe 区域。"""
    if isinstance(texts, str):
        texts = [texts]
    if isinstance(colors, str):
        colors = [colors]
    if not texts:
        raise ValueError("cover.texts 至少需要一项文字")
    colors = list(colors or ["#FFFFFF"])
    if len(colors) == 1:
        colors *= len(texts)
    if len(colors) != len(texts):
        raise ValueError("cover.colors 必须只传一个颜色，或与 cover.texts 数量一致")

    args = [
        json.dumps(list(texts), ensure_ascii=False),
        json.dumps(colors),
        image_path or "",
        str(frame_opacity),
        _edge_value(edge),
    ]
    script = Path(__file__).with_name("manimgl_text_cover_script.py")
    render_args = list(extra_args or [])
    if transparent and "-t" not in render_args:
        render_args.append("-t")
    return _render(
        script, "cover", args, output_dir, is_open, render_args, output_filename
    )

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='用 manimgl 渲染封面（调试用）')
    parser.add_argument('name')
    parser.add_argument('output_dir')
    parser.add_argument('--time', default='')
    parser.add_argument('--color', default='#83C167')
    parser.add_argument('--year', default='2026')
    parser.add_argument('--edge', type=int, choices=(-1, 0, 1), default=0)
    args = parser.parse_args()
    rendercover_with_manimgl(
        args.name, args.time, args.color, args.year, args.output_dir,
        edge=args.edge,
    )
