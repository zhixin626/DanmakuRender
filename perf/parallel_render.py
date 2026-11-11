"""Run isolated 30-minute GPU renders serially and in parallel with telemetry."""

from __future__ import annotations

import json
import pathlib
import statistics
import subprocess
import sys
import time
from collections import defaultdict

import psutil
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
PYTHON = ROOT / "venv" / "Scripts" / "python.exe"
WORK = ROOT / "perf" / "tmp" / "parallel_render"
CONFIG_DIR = WORK / "configs"
SOURCE = ROOT / "perf" / "samples" / "final_2h" / "原始" / "少年不太冷_11月9日14点20分.flv"
EVENTS = ROOT / "perf" / "samples" / "final_2h" / "补充" / "少年不太冷_11月9日14点20分_augmented.jsonl"
DURATION = 30 * 60
FPS = 60.0


def clip_events(source: pathlib.Path, target: pathlib.Path, duration: float) -> int:
    """Keep the original segment timeline while limiting events to the clip."""
    with source.open(encoding="utf8") as stream:
        header = json.loads(stream.readline())
        start = float(header.get("segment_start_time", 0) or 0)
        records = []
        for line in stream:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            timestamp = record.get("received_at")
            if isinstance(timestamp, (int, float)) and 0 <= float(timestamp) - start <= duration:
                records.append(record)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf8") as stream:
        stream.write(json.dumps(header, ensure_ascii=False) + "\n")
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    return len(records)


def prepare_inputs() -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    WORK.mkdir(parents=True, exist_ok=True)
    video = WORK / "source_30m.flv"
    events = WORK / "events_30m.jsonl"
    if not video.exists():
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-nostdin",
                "-i",
                str(SOURCE),
                "-t",
                str(DURATION),
                "-c",
                "copy",
                "-avoid_negative_ts",
                "make_zero",
                str(video),
            ],
            cwd=ROOT,
            check=True,
        )
    clip_events(EVENTS, events, DURATION)
    return video, events, write_config()


def write_config() -> pathlib.Path:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    base = yaml.safe_load((ROOT / "DMR" / "Config" / "default.yml").read_text(encoding="utf8"))
    base.setdefault("dmr_engine_args", {})["config_path"] = str(CONFIG_DIR)
    base.setdefault("render_args", {}).setdefault("overlay", {})["engine"] = "gpu_render"
    global_path = CONFIG_DIR / "global.yml"
    global_path.write_text(yaml.safe_dump(base, allow_unicode=True, sort_keys=False), encoding="utf8")
    (CONFIG_DIR / "DMR-parallel.yml").write_text(
        "render_args:\n  overlay:\n    engine: gpu_render\n", encoding="utf8"
    )
    return global_path


def descendants(process: psutil.Process) -> list[psutil.Process]:
    try:
        return [process, *process.children(recursive=True)]
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return [process]


def gpu_sample() -> dict[str, float]:
    try:
        values = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu,utilization.encoder,utilization.decoder,memory.used",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip().split(",")
        return {
            "gpu": float(values[0]),
            "encoder": float(values[1]),
            "decoder": float(values[2]),
            "memory_mb": float(values[3]),
        }
    except (OSError, ValueError, IndexError, subprocess.CalledProcessError):
        return {"gpu": 0.0, "encoder": 0.0, "decoder": 0.0, "memory_mb": 0.0}


def start_command(global_path: pathlib.Path, video: pathlib.Path, events: pathlib.Path, output: pathlib.Path) -> list[str]:
    return [
        str(PYTHON),
        str(ROOT / "render_only.py"),
        "--config",
        str(global_path),
        "--task",
        "parallel",
        "--video",
        str(video),
        "--danmaku",
        str(events),
        "--output",
        str(output),
    ]


def run_group(
    label: str,
    count: int,
    global_path: pathlib.Path,
    video: pathlib.Path,
    events: pathlib.Path,
    flicker_plan: pathlib.Path,
    serial: bool = False,
) -> dict[str, object]:
    group_dir = WORK / label
    group_dir.mkdir(parents=True, exist_ok=True)
    processes = []
    logs = []
    started = time.perf_counter()
    telemetry_by_index: list[dict[str, object]] = []
    for index in range(count):
        output = group_dir / f"render_{index + 1}.mp4"
        log = group_dir / f"render_{index + 1}.log"
        stream = log.open("w", encoding="utf8")
        command = start_command(global_path, video, events, output)
        process = subprocess.Popen(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT)
        processes.append((process, output, command))
        logs.append(stream)
        if serial:
            telemetry_by_index.append(wait_processes([processes[-1]], telemetry=True))
    if not serial:
        telemetry = wait_processes(processes, telemetry=True)
        telemetry_by_index = [telemetry] * len(processes)
    for stream in logs:
        stream.close()
    results = []
    for index, (process, output, command) in enumerate(processes, 1):
        results.append(
            {
                "index": index,
                "returncode": process.returncode,
                "output": str(output),
                "log": str(group_dir / f"render_{index}.log"),
                "command": command,
                "telemetry": telemetry_by_index[index - 1],
            }
        )
    return {"label": label, "count": count, "serial": serial, "wall_s": time.perf_counter() - started, "renders": results}


def wait_processes(processes, telemetry: bool = False) -> dict[str, object]:
    roots = {process.pid: psutil.Process(process.pid) for process, _, _ in processes}
    previous: dict[int, float] = {}
    cpu_samples: list[float] = []
    gpu_samples: list[dict[str, float]] = []
    while any(process.poll() is None for process, _, _ in processes):
        time.sleep(1.0)
        if not telemetry:
            continue
        now_cpu = 0.0
        for root in list(roots.values()):
            for child in descendants(root):
                try:
                    current = sum(child.cpu_times()[:2])
                    previous_value = previous.get(child.pid, current)
                    now_cpu += max(0.0, current - previous_value)
                    previous[child.pid] = current
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        cpu_samples.append(now_cpu)
        gpu_samples.append(gpu_sample())
    for process, _, _ in processes:
        process.wait()
    if telemetry:
        telemetry_result = {
            "cpu_avg_cores": statistics.mean(cpu_samples) if cpu_samples else 0.0,
            "cpu_peak_cores": max(cpu_samples, default=0.0),
            "cpu_core_seconds": sum(cpu_samples),
            "gpu_avg": {
                key: statistics.mean(sample[key] for sample in gpu_samples)
                for key in ("gpu", "encoder", "decoder")
            },
            "gpu_peak": {
                key: max((sample[key] for sample in gpu_samples), default=0.0)
                for key in ("gpu", "encoder", "decoder")
            },
            "vram_peak_mb": max((sample["memory_mb"] for sample in gpu_samples), default=0.0),
            "sample_count": len(cpu_samples),
        }
        return telemetry_result
    return {}


def probe(path: pathlib.Path) -> dict[str, object]:
    raw = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration,bit_rate:stream=codec_type,width,height,avg_frame_rate,nb_frames,bit_rate",
            "-of",
            "json",
            str(path),
        ],
        text=True,
    )
    return json.loads(raw)


def verify_output(path: pathlib.Path, source: pathlib.Path, plan: pathlib.Path) -> dict[str, object]:
    metadata = probe(path)
    stream = next(item for item in metadata["streams"] if item["codec_type"] == "video")
    audio = [item for item in metadata["streams"] if item["codec_type"] == "audio"]
    metadata_frames = int(float(stream.get("nb_frames") or 0))
    # The copied FLV can advertise one more container frame than ffmpeg can
    # decode after the final timestamp. Check every decodable output frame.
    frames = max(1, metadata_frames - 1)
    report = WORK / f"{path.parent.name}_{path.stem}_flicker.json"
    command = [
        str(PYTHON),
        str(ROOT / "perf" / "check_flicker.py"),
        "--source",
        str(source),
        "--gpu",
        str(path),
        "--plan",
        str(plan),
        "--start",
        "0",
        "--fps",
        str(FPS),
        "--frames",
        str(frames),
        "--report",
        str(report),
    ]
    check = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf8")
    flicker = json.loads(report.read_text(encoding="utf8")) if report.exists() else {"error": check.stderr[-1000:]}
    return {
        "path": str(path),
        "duration_s": float(metadata.get("format", {}).get("duration") or 0),
        "bitrate": float(stream.get("bit_rate") or metadata.get("format", {}).get("bit_rate") or 0),
        "frames": frames,
        "metadata_frames": metadata_frames,
        "audio_streams": len(audio),
        "flicker_exit": check.returncode,
        "flicker": {key: flicker.get(key) for key in ("complete", "flicker_frames", "missing_regions")},
    }


def main() -> int:
    sys.path.insert(0, str(ROOT))
    video, events, global_path = prepare_inputs()
    from DMR.Config import Config
    from DMR.Render.layout_compiler import LayoutCompiler
    from DMR.utils import FFprobe, VideoInfo

    config = Config(str(global_path))
    args = config.get_render_args("parallel", "overlay")
    plan_dir = WORK / "plan"
    plan_dir.mkdir(parents=True, exist_ok=True)
    compiler = LayoutCompiler(args, engine="gpu_render", engine_config=args)
    info = VideoInfo(
        path=str(video),
        dm_file_id=str(events),
        duration=FFprobe.get_timeline_duration(str(video)),
        resolution=FFprobe.get_resolution(str(video)),
    )
    compiled = compiler.compile(video=info, events_path=str(events), work_dir=str(plan_dir))
    plan = pathlib.Path(compiled.render_plan_path)
    groups = [
        run_group("serial_1", 1, global_path, video, events, plan, serial=True),
        run_group("parallel_2", 2, global_path, video, events, plan),
        run_group("parallel_3", 3, global_path, video, events, plan),
    ]
    baseline_wall = groups[0]["wall_s"]
    for group in groups[1:]:
        group["serial_estimate_s"] = baseline_wall * group["count"]
        group["speedup_vs_serial_estimate"] = (
            group["serial_estimate_s"] / group["wall_s"] if group["wall_s"] else 0.0
        )
    checks = []
    for group in groups:
        for render in group["renders"]:
            output = pathlib.Path(render["output"])
            if render["returncode"] == 0 and output.exists():
                checks.append(verify_output(output, video, plan))
    result = {"source": str(video), "events": str(events), "duration_requested_s": DURATION, "groups": groups, "checks": checks}
    (WORK / "parallel.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8")
    compiled.cleanup()
    return 0 if all(render["returncode"] == 0 for group in groups for render in group["renders"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
