"""Measure one long E/G render and repeated short-render CPU/GPU telemetry."""

from __future__ import annotations

import json
import pathlib
import statistics
import subprocess
import sys
import time
from collections import defaultdict

import psutil


ROOT = pathlib.Path(__file__).resolve().parents[1]
PYTHON = ROOT / "perf" / "venv" / "Scripts" / "python.exe"
OUT = ROOT / "perf" / "tmp" / "cpu_telemetry"
OUT.mkdir(parents=True, exist_ok=True)


def tree(pid: int) -> list[psutil.Process]:
    try:
        root = psutil.Process(pid)
    except psutil.Error:
        return []
    result = {root.pid: root}
    children = defaultdict(list)
    for item in psutil.process_iter(["pid", "ppid"]):
        children[item.info.get("ppid")].append(item)
    pending = [root.pid]
    while pending:
        parent = pending.pop()
        for item in children.get(parent, []):
            if item.pid not in result:
                result[item.pid] = item
                pending.append(item.pid)
    return list(result.values())


def proc_kind(process: psutil.Process, label: str) -> str:
    try:
        command = " ".join(process.cmdline()).lower()
        name = process.name().lower()
    except psutil.Error:
        return "other"
    if "ffmpeg" in name or "ffmpeg" in command:
        return "ffmpeg"
    if label == "E" and ("emoji_render_mt.py" in command or name.startswith("python")):
        return "python_worker"
    if label == "G" and name.startswith("python"):
        return "python_main"
    return "other"


def gpu_dmon() -> dict[str, float]:
    try:
        raw = subprocess.check_output(
            ["nvidia-smi", "dmon", "-s", "pucvmet", "-c", "1"],
            text=True, stderr=subprocess.DEVNULL,
        )
        for line in reversed(raw.splitlines()):
            cols = line.split()
            if len(cols) >= 10 and cols[0].isdigit():
                # dmon -s pucvmet: gpu pwr temp sm mem enc dec mclk pclk
                return {"gpu": float(cols[4]), "encoder": float(cols[6]), "decoder": float(cols[7])}
    except Exception:
        pass
    try:
        values = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=utilization.gpu,utilization.encoder,utilization.decoder", "--format=csv,noheader,nounits"],
            text=True, stderr=subprocess.DEVNULL,
        ).strip().split(",")
        return {"gpu": float(values[0]), "encoder": float(values[1]), "decoder": float(values[2])}
    except Exception:
        return {"gpu": 0.0, "encoder": 0.0, "decoder": 0.0}


def run(label: str, command: list[str], expected_frames: int, output: pathlib.Path) -> dict:
    started = time.perf_counter()
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    state: dict[int, psutil.Process] = {}
    for item in tree(process.pid):
        try:
            item.cpu_percent(None)
            state[item.pid] = item
        except psutil.Error:
            pass
    samples: list[float] = []
    components: dict[str, list[float]] = defaultdict(list)
    gpu_samples: list[dict[str, float]] = []
    output_log = []
    while process.poll() is None:
        time.sleep(1.0)
        total = 0.0
        current: dict[str, float] = defaultdict(float)
        for item in tree(process.pid):
            try:
                value = item.cpu_percent(None)
                total += value
                current[proc_kind(item, label)] += value
                state[item.pid] = item
            except psutil.Error:
                pass
        samples.append(total)
        for key, value in current.items():
            components[key].append(value)
        gpu_samples.append(gpu_dmon())
    process.wait()
    wall = time.perf_counter() - started
    result = {
        "label": label, "command": command, "output": str(output),
        "returncode": process.returncode, "wall_s": wall,
        "expected_frames": expected_frames, "fps": expected_frames / wall if wall else 0.0,
        "cpu_avg_percent": statistics.mean(samples) if samples else 0.0,
        "cpu_peak_percent": max(samples) if samples else 0.0,
        "avg_cores": (statistics.mean(samples) / 100) if samples else 0.0,
        "peak_cores": (max(samples) / 100) if samples else 0.0,
        "core_seconds": (statistics.mean(samples) / 100 * wall) if samples else 0.0,
        "component_avg_cores": {key: statistics.mean(values) / 100 for key, values in components.items() if values},
        "component_peak_cores": {key: max(values) / 100 for key, values in components.items() if values},
        "gpu_avg": {key: statistics.mean(item[key] for item in gpu_samples) for key in ("gpu", "encoder", "decoder")},
        "gpu_peak": {key: max(item[key] for item in gpu_samples) for key in ("gpu", "encoder", "decoder")},
        "samples": len(samples), "worker_output_tail": output_log,
    }
    if process.returncode:
        raise RuntimeError(json.dumps(result, ensure_ascii=False))
    probe = subprocess.check_output(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=bit_rate,nb_frames,r_frame_rate,duration", "-show_entries", "format=duration,size", "-of", "json", str(output)],
        text=True,
    )
    result["ffprobe"] = json.loads(probe)
    return result


def main() -> None:
    final_video = ROOT / "perf" / "samples" / "final_2h" / "原始" / "少年不太冷_11月9日14点20分.flv"
    plan = ROOT / "perf" / "tmp" / "final_2h_plan" / "render_plan.json"
    atlas = ROOT / "perf" / "tmp" / "final_2h_atlas"
    e_out = OUT / "E_final_2h.mp4"
    g_out = OUT / "G_final_2h.mp4"
    final_frames = 428635
    commands = [
        ("E_final_2h", [str(PYTHON), str(ROOT / "DMR" / "Render" / "emoji_render_mt.py"), str(final_video), str(plan), str(e_out), "--vencoder", "hevc_nvenc", "--aencoder", "aac", "--vb", "4M", "--ab", "128K", "--venc-args", '["-b:v","4M"]', "--aenc-args", '["-b:a","128K"]', "--emoji-pack", str(ROOT / "emoji_pack" / "douyu"), "--emoticon-dir", str(ROOT / "emoji_pack" / "douyu" / "emoticon"), "--canvas-resolution", "1920x1080", "--render-plan"], final_frames, e_out),
        ("G_final_2h", [str(PYTHON), str(ROOT / "perf" / "run_gpu_render.py"), "--video", str(final_video), "--output", str(g_out), "--vencoder", "hevc_nvenc", "--bitrate", "4000000", "--rc", "vbr", "--maxbitrate", "6000000", "--vbvbufsize", "8000000", "--preset", "P4", "--fps", "60", "--max-frames", str(final_frames), "--sprites", "91", "--render-plan", str(plan), "--sprite-data", str(atlas)], final_frames, g_out),
    ]
    results = [run(*command) for command in commands]
    short_video = ROOT / "演示视频" / "00_素材与旧版" / "bilibili_峰哥_7月30日20点23分_中段" / "7月30日20点23分_中段.flv"
    short_plan = ROOT / "perf" / "tmp" / "bilibili_峰哥_7月30日20点23分_中段" / "render_plan.json"
    short_atlas = ROOT / "perf" / "tmp" / "bilibili_峰哥_7月30日20点23分_中段" / "atlas"
    for index in range(3):
        for label, command, frames, output in (
            (f"E_short_{index + 1}", [str(PYTHON), str(ROOT / "DMR" / "Render" / "emoji_render_mt.py"), str(short_video), str(short_plan), str(OUT / f"E_short_{index + 1}.mp4"), "--vencoder", "hevc_nvenc", "--aencoder", "aac", "--vb", "4M", "--ab", "128K", "--venc-args", '["-b:v","4M"]', "--aenc-args", '["-b:a","128K"]', "--emoji-pack", str(ROOT / "emoji_pack" / "bilibili"), "--emoticon-dir", str(ROOT / "emoji_pack" / "bilibili" / "emoticon"), "--canvas-resolution", "1080x1920", "--render-plan"], 4500, OUT / f"E_short_{index + 1}.mp4"),
            (f"G_short_{index + 1}", [str(PYTHON), str(ROOT / "perf" / "run_gpu_render.py"), "--video", str(short_video), "--output", str(OUT / f"G_short_{index + 1}.mp4"), "--vencoder", "hevc_nvenc", "--bitrate", "4000000", "--rc", "vbr", "--maxbitrate", "6000000", "--vbvbufsize", "8000000", "--preset", "P4", "--fps", "25", "--max-frames", "4500", "--sprites", "91", "--render-plan", str(short_plan), "--sprite-data", str(short_atlas)], 4500, OUT / f"G_short_{index + 1}.mp4"),
        ):
            results.append(run(label, command, frames, output))
    (OUT / "telemetry.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
