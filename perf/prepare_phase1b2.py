"""Compile persistent RenderPlans for two configurable style variants."""

from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import sys

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
VIDEO = ROOT / "perf" / "samples" / "full_coverage" / "峰哥_8月18日14点19分_1860-2160.flv"
JSONL = ROOT / "perf" / "samples" / "full_coverage" / "峰哥_8月18日14点19分_1860-2160_augmented.jsonl"
BASE_CONFIG = ROOT / "perf" / "tmp" / "full_coverage_config"


def copy_config(source: pathlib.Path, destination: pathlib.Path, variant: bool) -> pathlib.Path:
    """Copy the approved test config and apply only variant-B style changes."""
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    global_config = yaml.safe_load((source / "global.yml").read_text(encoding="utf-8"))
    task_config = yaml.safe_load((source / "DMR-峰哥.yml").read_text(encoding="utf-8"))
    global_config["dmr_engine_args"]["config_path"] = str(destination)
    # Compare E and G at the source 25 fps; CFR 25->50 belongs to 1b-3.
    global_config["render_args"]["overlay"]["min_fps"] = None
    if variant:
        overlay = global_config["render_args"]["overlay"]
        danmaku = overlay["r2l"]["danmaku"]
        overlay["r2l"]["font"] = "SimSun"
        overlay["r2l"]["fontsize"] = 48
        overlay["r2l"]["opacity"] = 0.9
        overlay["r2l"]["outline_size"] = "3px"
        overlay["r2l"]["outline_color"] = "#202020"
        overlay["r2l"]["vertical_gap"] = "16px"
        danmaku["font"] = "SimSun"
        danmaku["fontsize"] = 48
        danmaku["opacity"] = 0.9
        danmaku["outline_size"] = "3px"
        danmaku["outline_color"] = "#202020"
        overlay["slide_left"]["fontsize"] = 26
        overlay["slide_left"]["top_opacity"] = 0.85
        overlay["slide_left"]["font"] = "SimSun"
        overlay["slide_left"]["name_color"] = "#2E5D9F"
        overlay["slide_left"]["content_color"] = "#FFF2C7"
        overlay["slide_left"]["corner_radius"] = "24px"
        overlay["slide_right"]["fontsize"] = 26
        overlay["slide_right"]["top_opacity"] = 0.85
        overlay["slide_right"]["font"] = "SimSun"
        overlay["slide_right"]["name_color"] = "#2E5D9F"
        overlay["slide_right"]["content_color"] = "#FFF2C7"
        overlay["slide_right"]["corner_radius"] = "24px"
        task_config["render_args"]["overlay"]["r2l"]["screen_duration"] = 12
    (destination / "global.yml").write_text(
        yaml.safe_dump(global_config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    (destination / "DMR-峰哥.yml").write_text(
        yaml.safe_dump(task_config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return destination / "global.yml"


def compile_plan(config_path: pathlib.Path, output: pathlib.Path) -> dict:
    """Compile one plan with the same Config/LayoutCompiler path as production."""
    sys.path.insert(0, str(ROOT))
    from DMR.Config import Config
    from DMR.Render.layout_compiler import LayoutCompiler
    from DMR.utils import FFprobe, VideoInfo

    config = Config(str(config_path))
    task = config.get_task_config("峰哥")
    render_args = dict(task["render_args"]["overlay"])
    render_args["engine"] = "gpu_render"
    video = VideoInfo(
        path=str(VIDEO),
        dm_file_id=str(JSONL),
        duration=FFprobe.get_timeline_duration(str(VIDEO)),
        resolution=FFprobe.get_resolution(str(VIDEO)),
    )
    result = LayoutCompiler(
        render_args,
        engine="gpu_render",
        engine_config=render_args,
    ).compile(video=video, events_path=str(JSONL), work_dir=str(output.parent))
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(result.render_plan_path, output)
    summary = {
        "plan": str(output),
        "events": result.event_count,
        "dropped": result.dropped_count,
        "track_stats": result.track_stats,
        "duration": video.duration,
    }
    result.cleanup()
    return summary


def main() -> None:
    """Create baseline and variant-B configs and persistent plans."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=pathlib.Path, default=ROOT / "perf" / "tmp" / "phase1b2")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    baseline_config = copy_config(BASE_CONFIG, args.output / "config_baseline", variant=False)
    variant_config = copy_config(BASE_CONFIG, args.output / "config_variant_b", variant=True)
    summaries = [
        compile_plan(baseline_config, args.output / "baseline" / "render_plan.json"),
        compile_plan(variant_config, args.output / "variant_b" / "render_plan.json"),
    ]
    (args.output / "summary.json").write_text(
        json.dumps(summaries, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summaries, ensure_ascii=False))


if __name__ == "__main__":
    main()
