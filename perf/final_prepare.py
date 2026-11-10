"""Compile a fresh final_2h RenderPlan from the isolated config snapshots."""

from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from DMR.Config import Config
from DMR.Render.layout_compiler import LayoutCompiler
from DMR.utils import FFprobe, VideoInfo


if __name__ == "__main__":
    sample = ROOT / "perf/samples/final_2h"
    video_path = sample / "原始/少年不太冷_11月9日14点20分.flv"
    events_path = sample / "补充/少年不太冷_11月9日14点20分_augmented.jsonl"
    work = ROOT / "perf/tmp/final_2h_plan_v2"
    work.mkdir(parents=True, exist_ok=True)
    config = Config(str(sample / "配置/global.yml"))
    config.config_dir = str(sample / "配置")
    options = config.get_render_args("少年不太冷")
    video = VideoInfo(
        path=str(video_path), dm_file_id=str(events_path),
        duration=FFprobe.get_timeline_duration(str(video_path)),
        resolution=FFprobe.get_resolution(str(video_path)),
    )
    compiled = LayoutCompiler(options, engine="gpu_render", engine_config=options).compile(
        video=video, events_path=str(events_path), work_dir=str(work)
    )
    plan = work / "render_plan.json"
    plan.write_bytes(Path(compiled.render_plan_path).read_bytes())
    (work / "options.json").write_text(json.dumps(options, ensure_ascii=False, indent=2), encoding="utf8")
    print(json.dumps({"plan": str(plan), "duration": video.duration, "canvas": compiled.next_state.canvas_resolution}, ensure_ascii=False))
    compiled.cleanup()
