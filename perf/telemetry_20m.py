from __future__ import annotations

import json
import pathlib
import subprocess
import time

import psutil

ROOT = pathlib.Path(__file__).resolve().parents[1]
PYTHON = ROOT / "venv" / "Scripts" / "python.exe"
CASE = ROOT / "perf" / "tmp" / "telemetry_20m" / "telemetry_20m"
VIDEO = CASE / "少年不太冷_11月9日14点20分_clip.flv"
PLAN = CASE / "render_plan.json"
ATLAS = CASE / "atlas"
OUT = ROOT / "perf" / "tmp" / "telemetry_20m_results"
OUT.mkdir(parents=True, exist_ok=True)


def descendants(root: psutil.Process) -> list[psutil.Process]:
    try:
        return [root, *root.children(recursive=True)]
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return [root]


def gpu_sample() -> dict[str, float]:
    try:
        values = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=utilization.gpu,utilization.encoder,utilization.decoder", "--format=csv,noheader,nounits"],
            text=True, stderr=subprocess.DEVNULL,
        ).strip().split(",")
        return {"gpu": float(values[0]), "encoder": float(values[1]), "decoder": float(values[2])}
    except Exception:
        return {"gpu": 0.0, "encoder": 0.0, "decoder": 0.0}


def run(label: str, command: list[str]) -> dict[str, object]:
    output = OUT / f"{label}.mp4"
    if "--output" in command:
        command = [str(value).replace("{OUTPUT}", str(output)) for value in command]
    started = time.perf_counter()
    process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    root = psutil.Process(process.pid)
    samples: list[float] = []
    gpu: list[dict[str, float]] = []
    previous: dict[int, float] = {}
    previous_wall = time.perf_counter()
    while process.poll() is None:
        time.sleep(1.0)
        now_wall = time.perf_counter()
        total_delta = 0.0
        for child in descendants(root):
            try:
                current = sum(child.cpu_times()[:2])
                before = previous.get(child.pid, current)
                total_delta += max(0.0, current - before)
                previous[child.pid] = current
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        elapsed_wall = max(1e-6, now_wall - previous_wall)
        samples.append(total_delta / elapsed_wall)
        previous_wall = now_wall
        gpu.append(gpu_sample())
    elapsed = time.perf_counter() - started
    avg = sum(samples) / len(samples) if samples else 0.0
    result = {
        "label": label,
        "returncode": process.returncode,
        "wall_s": elapsed,
        "avg_cores": avg,
        "peak_cores": max(samples) if samples else 0.0,
        "core_seconds": sum(samples),
        "gpu_avg": {key: sum(item[key] for item in gpu) / len(gpu) for key in ("gpu", "encoder", "decoder")} if gpu else {},
        "output": str(output),
    }
    print(json.dumps(result, ensure_ascii=False))
    return result


results = [
    run("E_20m", [str(PYTHON), str(ROOT / "DMR" / "Render" / "emoji_render_mt.py"), str(VIDEO), str(PLAN), str(OUT / "E_20m.mp4"), "--vencoder", "hevc_nvenc", "--aencoder", "aac", "--vb", "4M", "--ab", "128K", "--venc-args", '["-b:v","4M"]', "--aenc-args", '["-b:a","128K"]', "--emoji-pack", str(ROOT / "emoji_pack" / "douyu"), "--emoticon-dir", str(ROOT / "emoji_pack" / "douyu" / "emoticon"), "--canvas-resolution", "1920x1080", "--render-plan"]),
    run("G_20m", [str(PYTHON), str(ROOT / "perf" / "run_gpu_render.py"), "--video", str(VIDEO), "--output", str(OUT / "G_20m.mp4"), "--vencoder", "hevc_nvenc", "--bitrate", "4000000", "--rc", "vbr", "--maxbitrate", "6000000", "--vbvbufsize", "8000000", "--preset", "P4", "--fps", "60", "--max-frames", "72000", "--render-plan", str(PLAN)]),
]
(OUT / "telemetry.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
