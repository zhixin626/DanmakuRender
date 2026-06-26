import logging
import subprocess
from pathlib import Path
logger=logging.getLogger(__name__)

def rendercover_with_manimgl(name,time,color,year,output_dir,image_path=None,is_open=False,extra_args=None,output_filename=None):
    """
    使用manimgl渲染封面，保存路径 Rf"D:\\DanmakuRender\\{name}"
    """
    manimgl_exe = R"D:\manim\venv\Scripts\manimgl.exe"
    script = R"D:\DanmakuRender\DMR\utils\manimgl_cover_script.py"
    scene_name = "cover"

    args = [name, time, color, year, image_path or ""]

    cmd = [manimgl_exe, script, scene_name] + args + [
        "-s",
        "-w" if not is_open else "-o",
        "--video_dir", output_dir,
        "-q",
    ]
    if extra_args:
        cmd += list(extra_args)

    subprocess.run(cmd, check=True)

    rendered_path = Path(output_dir) / "cover.png"

    if output_filename and output_filename != "cover.png":
        target_path = Path(output_dir) / output_filename
        rendered_path.replace(target_path)
        return str(target_path)

    return str(rendered_path)

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='用 manimgl 渲染封面（调试用）')
    parser.add_argument('name')
    parser.add_argument('output_dir')
    parser.add_argument('--time', default='')
    parser.add_argument('--color', default='#83C167')
    parser.add_argument('--year', default='2026')
    args = parser.parse_args()
    rendercover_with_manimgl(args.name, args.time, args.color, args.year, args.output_dir)