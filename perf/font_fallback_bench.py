"""Compare one isolated GPU render before and after the font fallback chain."""

from __future__ import annotations

import json
import pathlib
import subprocess
import time

import psutil

ROOT = pathlib.Path(__file__).resolve().parents[1]
PYTHON = ROOT / "venv" / "Scripts" / "python.exe"
WORK = ROOT / "perf/tmp/invisible_demo"
VIDEO = WORK / "source_60s.flv"
EVENTS = WORK / "invisible_demo.jsonl"


def run(label: str, root: pathlib.Path, config: pathlib.Path, output: pathlib.Path) -> dict:
    output.unlink(missing_ok=True)
    command = [
        str(PYTHON),
        str(root / "render_only.py"),
        "--config",
        str(config),
        "--task",
        "demo-gpu_render",
        "--video",
        str(VIDEO),
        "--danmaku",
        str(EVENTS),
        "--output",
        str(output),
    ]
    started = time.perf_counter()
    process = subprocess.Popen(command, cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    root_process = psutil.Process(process.pid)
    previous = {}
    samples = []
    while process.poll() is None:
        time.sleep(0.5)
        now = 0.0
        try:
            children = root_process.children(recursive=True)
        except psutil.NoSuchProcess:
            children = []
        for child in [root_process, *children]:
            try:
                current = sum(child.cpu_times()[:2])
                before = previous.get(child.pid, current)
                now += max(0.0, current - before) / 0.5
                previous[child.pid] = current
            except psutil.Error:
                pass
        samples.append(now)
    process.wait()
    result = {
        "label": label,
        "returncode": process.returncode,
        "wall_s": time.perf_counter() - started,
        "cpu_avg_cores": (sum(samples) / len(samples)) if samples else 0.0,
        "cpu_peak_cores": max(samples, default=0.0),
        "output": str(output),
    }
    return result


def main() -> int:
    old_root = ROOT / "perf/tmp/old9"
    results = [
        run(
            "before",
            old_root,
            old_root / "perf/tmp/invisible_demo/configs/global.yml",
            WORK / "G_font_before.mp4",
        ),
        run(
            "after",
            ROOT,
            WORK / "configs/global.yml",
            WORK / "G_font_after.mp4",
        ),
        run(
            "after_no_fallback",
            ROOT,
            WORK / "configs/global_no_fallback.yml",
            WORK / "G_font_after_no_fallback.mp4",
        ),
    ]
    (WORK / "font_fallback_bench.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf8"
    )
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if all(item["returncode"] == 0 for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
