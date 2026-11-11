"""Run frame checks over each of the twelve scenario windows."""

from pathlib import Path
import json
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
source = ROOT / "演示视频/00_素材与旧版/bilibili_峰哥_7月30日20点23分_中段/7月30日20点23分_中段.flv"
delivery = next(
    path for path in (ROOT / "演示视频").glob("2026-11-11_14*") if path.is_dir()
)
gpu = next(path for path in delivery.glob("*.mp4") if "GPU" in path.name or "gpu" in path.name)
plan = next(path for path in delivery.glob("*GPU*plan.json"))
scenarios = [
    ("01_push", 4.8, 7.2), ("02_fall", 19.8, 22.2),
    ("03_entry", 34.8, 35.4), ("04_multi", 49.8, 51.2),
    ("05_different", 64.8, 67.2), ("06_boundary", 79.8, 85.3),
    ("07_long", 94.8, 99.9), ("08_count10", 109.8, 112.2),
    ("09_tiers", 124.8, 129.2), ("10_small", 139.8, 143.2),
    ("11_tid", 154.8, 158.2), ("12_pile", 171.8, 177.2),
]
results = {}
for name, start, end in scenarios:
    report = ROOT / "perf/tmp/night" / f"scenario_{name}_flicker.json"
    command = [
        sys.executable, "perf/check_flicker.py", "--source", str(source),
        "--gpu", str(gpu), "--plan", str(plan), "--fps", "50",
        "--frames", str(int(round((end - start) * 50)) + 1),
        "--start", str(start), "--canvas", "1080x1920", "--report", str(report),
    ]
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    try:
        summary = json.loads(completed.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        summary = {"returncode": completed.returncode, "stderr": completed.stderr[-500:]}
    results[name] = summary
(ROOT / "perf/tmp/night/scenario_flicker_summary.json").write_text(
    json.dumps(results, ensure_ascii=False, indent=2), encoding="utf8"
)
print(json.dumps(results, ensure_ascii=False, indent=2))
