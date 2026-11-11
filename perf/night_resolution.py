"""Run the G-stage real problem_resolution render in an isolated delivery folder."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from perf.round_regression import render_coverage


if __name__ == "__main__":
    source = ROOT / "perf/samples/problem_resolution/problem_resolution_7月30日22点50分/7月30日22点50分_clip.flv"
    events = source.with_suffix(".jsonl")
    delivery = ROOT / "演示视频/2026-11-11_10_分辨率时间线/problem_resolution"
    delivery.mkdir(parents=True, exist_ok=True)
    raise SystemExit(
        render_coverage(
            only="gpu_render",
            delivery=delivery,
            video_path=source,
            events_path=events,
            rescale=True,
            names=[("01_problem_resolution_GPU_G", "gpu_render")],
            config_name="G分辨率策略配置",
        )
    )
