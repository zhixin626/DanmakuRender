"""Profile one isolated Bilibili middle-segment GPU render."""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORK = ROOT / "perf/tmp/pipeline_profile"
SOURCE = ROOT / "演示视频/00_素材与旧版/bilibili_峰哥_7月30日20点23分_中段/7月30日20点23分_中段.flv"
EVENTS = ROOT / "演示视频/00_素材与旧版/bilibili_峰哥_7月30日20点23分_中段/7月30日20点23分_中段_augmented.jsonl"


def prepare() -> tuple[pathlib.Path, pathlib.Path]:
    WORK.mkdir(parents=True, exist_ok=True)
    video = WORK / "source.flv"
    if not video.exists():
        subprocess.run(
            [
                "ffmpeg", "-v", "error", "-y", "-nostdin", "-i", str(SOURCE),
                "-c", "copy", "-avoid_negative_ts", "make_zero", str(video),
            ],
            cwd=ROOT,
            check=True,
        )
    events = WORK / "events.jsonl"
    if not events.exists():
        events.write_bytes(EVENTS.read_bytes())
    return video, events


def main() -> int:
    video, events = prepare()
    output = WORK / "profile.mp4"
    profile = WORK / "pipeline_profile.json"
    config = ROOT / "perf/tmp/invisible_demo/configs/global.yml"
    command = [
        str(ROOT / "venv/Scripts/python.exe"),
        str(ROOT / "render_only.py"),
        "--config", str(config),
        "--task", "demo-gpu_render",
        "--video", str(video),
        "--danmaku", str(events),
        "--output", str(output),
    ]
    env = dict(os.environ)
    env["DMR_PIPELINE_PROFILE"] = "1"
    env["DMR_PIPELINE_PROFILE_OUTPUT"] = str(profile)
    started = time.perf_counter()
    completed = subprocess.run(command, cwd=ROOT, env=env)
    result = {
        "returncode": completed.returncode,
        "wall_s": time.perf_counter() - started,
        "video": str(video),
        "profile": str(profile),
    }
    if profile.exists():
        result["pipeline"] = json.loads(profile.read_text(encoding="utf8"))
    (WORK / "run.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
