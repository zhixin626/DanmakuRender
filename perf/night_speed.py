"""Render equal-time native-pixel landscape/portrait scrolling demonstrations."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from DMR.Config import Config
from DMR.Render import get_engine
from DMR.Render.layout_compiler import LayoutCompiler
from DMR.utils import FFprobe, VideoInfo
from perf.dmr_gate import decide
from perf.round_regression import MATERIAL, VIDEO, EVENTS
from perf.round_visuals import frame


def main():
    gate = decide(10)
    if not gate["go"]:
        print(json.dumps(gate, ensure_ascii=False))
        return 10
    delivery = ROOT / "演示视频/2026-11-11_09_固定滚动速度"
    delivery.mkdir(exist_ok=True)
    work = ROOT / "perf/tmp/night/speed"
    work.mkdir(parents=True, exist_ok=True)
    config = Config(str(MATERIAL / "配置/global.yml"))
    config.config_dir = str(MATERIAL / "配置")
    base = config.get_render_args("峰哥")
    base["r2l"]["speed"] = 240
    results = {}
    for number, orientation in enumerate(("portrait", "landscape"), 1):
        options = copy.deepcopy(base)
        options.update(rescale_to_1080p=True, rescale_orientation=orientation)
        video = VideoInfo(
            path=str(VIDEO), dm_file_id=str(EVENTS),
            duration=FFprobe.get_timeline_duration(str(VIDEO)),
            resolution=FFprobe.get_resolution(str(VIDEO)),
        )
        compiled = LayoutCompiler(options, engine="gpu_render", engine_config=options).compile(
            video=video, events_path=str(EVENTS), work_dir=str(work)
        )
        stem = f"{number:02d}_{orientation}_G"
        plan = delivery / f"{stem}_plan.json"
        plan.write_bytes(Path(compiled.render_plan_path).read_bytes())
        output = delivery / f"{stem}.mp4"
        renderer = get_engine("gpu_render").load()(**options)
        try:
            ok, info = renderer.render_one(
                video, str(output), render_plan=str(plan),
                canvas_resolution=compiled.next_state.canvas_resolution,
                max_frames=1500, fps=50,
            )
            if not ok:
                raise RuntimeError(str(info))
        finally:
            compiled.cleanup()
        results[orientation] = {
            "output": str(output), "canvas": list(info.resolution),
            "speed": 240, "seconds": 30,
        }
        frames = [frame(output, t) for t in (3, 15, 27)]
        import numpy as np

        results[orientation]["motion_mad"] = [
            float(np.abs(np.asarray(a, dtype=float) - np.asarray(b, dtype=float)).mean())
            for a, b in zip(frames, frames[1:])
        ]
        shots = ROOT / "perf/results/round_shots"
        for t, picture in zip((3, 15, 27), frames):
            picture.save(shots / f"09_{orientation}_{t}s.png")
        subprocess.run([
            sys.executable, "perf/check_flicker.py", "--source", str(VIDEO),
            "--gpu", str(output), "--plan", str(plan), "--fps", "50",
            "--frames", "1500", "--start", "0", "--canvas", "x".join(map(str, info.resolution)),
            "--report", str(work / f"{orientation}_flicker.json"),
        ], cwd=ROOT, check=True)
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-i", results["portrait"]["output"],
        "-i", results["landscape"]["output"], "-filter_complex",
        "[0:v]crop=1080:360:0:0,pad=1920:360:0:0,"
        "drawtext=text='Portrait - 240 px/s - native pixels':fontcolor=yellow:fontsize=24:x=20:y=320[p];"
        "[1:v]crop=1920:360:0:0,"
        "drawtext=text='Landscape - 240 px/s - native pixels':fontcolor=yellow:fontsize=24:x=20:y=320[l];"
        "[p][l]vstack[v]", "-map", "[v]", "-map", "0:a:0", "-c:v", "hevc_nvenc",
        "-b:v", "6M", "-c:a", "copy", str(delivery / "03_横竖屏原像素速度对比.mp4"),
    ], check=True)
    (work / "result.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf8")
    (delivery / "说明.txt").write_text(
        "来源：B站峰哥7月30日20点23分中段的0–30秒，同一份augmented弹幕。\n"
        "01竖屏、02横屏：均r2l.speed=240px/s，50fps、1500帧；源视频没有变更。\n"
        "03上下对比：上竖屏、下横屏，只裁取顶部360像素；竖屏右侧补黑，完全不缩放文字。"
        "看文字每秒都移动240像素，竖屏更早离开画面；长短文字速度一致。\n"
        "除画布方向外两份配置完全相同。\n", encoding="utf8",
    )
    print(json.dumps(results, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
