"""Compare RenderPlans from the pre-refactor and current LayoutCompiler."""

from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile

import yaml
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from DMR.Config import Config
from DMR.Render.layout_compiler import LayoutCompiler
from DMR.Render.layout_models import RenderPlan
from DMR.utils import FFprobe, VideoInfo


CASES = [
    {
        "name": "bilibili_middle_augmented",
        "video": ROOT / "演示视频/00_素材与旧版/bilibili_峰哥_7月30日20点23分_中段/7月30日20点23分_中段.flv",
        "events": ROOT / "演示视频/00_素材与旧版/bilibili_峰哥_7月30日20点23分_中段/7月30日20点23分_中段_augmented.jsonl",
        "task": "峰哥",
    },
    {
        "name": "final_2h_segment",
        "video": ROOT / "perf/samples/final_2h/原始/少年不太冷_11月9日14点20分.flv",
        "events": ROOT / "perf/samples/final_2h/补充/少年不太冷_11月9日14点20分_augmented.jsonl",
        "task": "少年不太冷",
    },
    {
        "name": "problem_resolution",
        "video": ROOT / "perf/samples/problem_resolution/problem_resolution_7月30日22点50分/7月30日22点50分_clip.flv",
        "events": ROOT / "perf/samples/problem_resolution/problem_resolution_7月30日22点50分/7月30日22点50分_clip.jsonl",
        "task": None,
    },
]


def load_before():
    target = ROOT / "perf/tmp/layout_compiler_before.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    source = subprocess.check_output(
        ["git", "show", "HEAD^:DMR/Render/layout_compiler.py"],
        cwd=ROOT,
    )
    target.write_bytes(source)
    spec = importlib.util.spec_from_file_location(
        "DMR.Render.layout_compiler_before", target
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.LayoutCompiler


def render_snapshot(compiler_class, args, case, work_dir):
    video_path = str(case["video"])
    video = VideoInfo(
        path=video_path,
        dm_file_id=str(case["events"]),
        duration=FFprobe.get_timeline_duration(video_path),
        resolution=FFprobe.get_resolution(video_path),
    )
    result = compiler_class(
        args,
        engine="gpu_render",
        engine_config=args,
    ).compile(video=video, events_path=str(case["events"]), work_dir=str(work_dir))
    plan = json.loads(pathlib.Path(result.render_plan_path).read_text(encoding="utf8"))
    state = result.next_state.to_dict()
    summary = {
        "event_count": result.event_count,
        "dropped_count": result.dropped_count,
        "track_stats": result.track_stats,
        "unrouted_dropped_count": result.unrouted_dropped_count,
        "plan": plan,
        "next_state": state,
    }
    result.cleanup()
    return summary


def main() -> int:
    snapshot = ROOT / "perf/configs_snapshot/global.yml"
    config_data = yaml.safe_load(snapshot.read_text(encoding="utf8"))
    config_data.setdefault("dmr_engine_args", {})["config_path"] = str(snapshot.parent)
    isolated_global = ROOT / "perf/tmp/layout_refactor_global.yml"
    isolated_global.parent.mkdir(parents=True, exist_ok=True)
    isolated_global.write_text(
        yaml.safe_dump(config_data, allow_unicode=True, sort_keys=False), encoding="utf8"
    )
    config = Config(str(isolated_global))
    before = load_before()
    results = []
    with tempfile.TemporaryDirectory(prefix="layout_refactor_", dir=ROOT / "perf/tmp") as temp:
        temp_root = pathlib.Path(temp)
        for case in CASES:
            if case["task"]:
                args = config.get_render_args(case["task"], "overlay")
            else:
                args = dict(config.global_config["render_args"]["overlay"])
            args["engine"] = "gpu_render"
            old = render_snapshot(before, args, case, temp_root / f"{case['name']}_before")
            new = render_snapshot(LayoutCompiler, args, case, temp_root / f"{case['name']}_after")
            equal = old == new
            results.append(
                {
                    "name": case["name"],
                    "equal": equal,
                    "before": {key: old[key] for key in ("event_count", "dropped_count", "track_stats", "unrouted_dropped_count")},
                    "after": {key: new[key] for key in ("event_count", "dropped_count", "track_stats", "unrouted_dropped_count")},
                }
            )
            if not equal:
                print(f"{case['name']}: RenderPlan mismatch")
                return 1
    print(json.dumps(results, ensure_ascii=False, indent=2))
    output = ROOT / "perf/tmp/layout_refactor_compare.json"
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
