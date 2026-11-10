"""Measure the real RenderPlan GPU run with process and GPU telemetry."""

from __future__ import annotations

import json
import pathlib
import statistics
import subprocess
import time

import psutil


ROOT = pathlib.Path(__file__).resolve().parents[1]
PYTHON = ROOT / "perf" / "venv" / "Scripts" / "python.exe"
LAUNCHER = ROOT / "perf" / "run_gpu_render.py"
VIDEO = ROOT / "perf" / "samples" / "normal_dense" / "normal_dense_peak_7月30日20点23分" / "7月30日20点23分_clip.flv"
PLAN = VIDEO.parent / "render_plan.json"
SPRITES = ROOT / "perf" / "tmp" / "phase1b_unified"
OUTPUT = ROOT / "perf" / "outputs" / "phase1b" / "normal_dense" / "normal_dense_peak_7月30日20点23分" / "G_gpu.mp4"


def process_tree(pid: int) -> list[psutil.Process]:
    """Return a process and all descendants by parent PID."""
    try:
        root = psutil.Process(pid)
    except psutil.Error:
        return []
    processes = {root.pid: root}
    children: dict[int, list[psutil.Process]] = {}
    for process in psutil.process_iter(["pid", "ppid"]):
        children.setdefault(process.info.get("ppid"), []).append(process)
    pending = [root.pid]
    while pending:
        parent = pending.pop()
        for child in children.get(parent, []):
            if child.pid not in processes:
                processes[child.pid] = child
                pending.append(child.pid)
    return list(processes.values())


def background_cpu() -> float:
    """Sample idle system CPU for the required ten seconds."""
    psutil.cpu_percent(None)
    samples = []
    until = time.monotonic() + 10
    while time.monotonic() < until:
        time.sleep(0.5)
        samples.append(psutil.cpu_percent(None))
    return statistics.mean(samples) if samples else 0.0


def gpu_sample() -> list[float]:
    """Read GPU, encoder, and decoder utilization."""
    try:
        values = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=utilization.gpu,utilization.encoder,utilization.decoder", "--format=csv,noheader,nounits"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip().split(",")
        return [float(value.strip()) for value in values]
    except Exception:
        return [0.0, 0.0, 0.0]


def main() -> None:
    """Run one measured GPU render and write JSON telemetry."""
    command = [
        str(PYTHON),
        str(LAUNCHER),
        "--video",
        str(VIDEO),
        "--output",
        str(OUTPUT),
        "--vencoder",
        "hevc_nvenc",
        "--bitrate",
        "4000000",
        "--rc",
        "vbr",
        "--maxbitrate",
        "6000000",
        "--vbvbufsize",
        "8000000",
        "--preset",
        "P4",
        "--max-frames",
        "7500",
        "--sprites",
        "91",
        "--render-plan",
        str(PLAN),
        "--sprite-data",
        str(SPRITES),
    ]
    idle_cpu = background_cpu()
    started = time.perf_counter()
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    state: dict[int, psutil.Process] = {}
    for child in process_tree(process.pid):
        try:
            child.cpu_percent(None)
            state[child.pid] = child
        except psutil.Error:
            pass
    cpu_samples = []
    gpu_samples = []
    while process.poll() is None:
        time.sleep(0.5)
        total = 0.0
        for child in process_tree(process.pid):
            try:
                previous = state.get(child.pid)
                if previous is not None:
                    total += previous.cpu_percent(None)
                else:
                    child.cpu_percent(None)
                state[child.pid] = child
            except psutil.Error:
                pass
        cpu_samples.append(total)
        gpu_samples.append(gpu_sample())
    output, _ = process.communicate()
    elapsed = time.perf_counter() - started
    result = {
        "command": command,
        "background_cpu_percent": idle_cpu,
        "wall_s": elapsed,
        "cpu_avg_percent": statistics.mean(cpu_samples) if cpu_samples else 0.0,
        "cpu_peak_percent": max(cpu_samples) if cpu_samples else 0.0,
        "avg_cores": statistics.mean(cpu_samples) / 100 if cpu_samples else 0.0,
        "peak_cores": max(cpu_samples) / 100 if cpu_samples else 0.0,
        "core_seconds": statistics.mean(cpu_samples) / 100 * elapsed if cpu_samples else 0.0,
        "gpu_avg_percent": statistics.mean(item[0] for item in gpu_samples) if gpu_samples else 0.0,
        "gpu_peak_percent": max(item[0] for item in gpu_samples) if gpu_samples else 0.0,
        "encoder_avg_percent": statistics.mean(item[1] for item in gpu_samples) if gpu_samples else 0.0,
        "decoder_avg_percent": statistics.mean(item[2] for item in gpu_samples) if gpu_samples else 0.0,
        "returncode": process.returncode,
        "worker_output": output,
    }
    (ROOT / "perf" / "tmp" / "phase1b_bench.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
