"""Detect frames where an expected GPU overlay disappears relative to source."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import numpy as np


def frame_stream(path: Path, fps: float, frames: int):
    """Decode a bounded stream once instead of spawning ffmpeg per frame."""
    width, height = 270, 480
    process = subprocess.Popen(
        [
            "ffmpeg", "-v", "error", "-i", str(path), "-frames:v", str(frames),
            "-vf", f"fps={fps},scale={width}:{height},format=rgb24",
            "-f", "rawvideo", "-",
        ],
        stdout=subprocess.PIPE,
    )
    size = width * height * 3
    last = None
    try:
        for _ in range(frames):
            data = process.stdout.read(size)
            if len(data) != size:
                if last is None:
                    break
                yield last.copy()
                continue
            last = np.frombuffer(data, dtype=np.uint8).reshape(height, width, 3).astype(np.float32)
            yield last
    finally:
        process.stdout.close()
        process.wait()


def active_regions(plan: dict, seconds: float) -> list[tuple[int, int, int, int]]:
    """Return visible event regions, clipping off-screen panels before measuring."""
    boxes = []
    for event in plan.get("events", []):
        path = event.get("path") or []
        if not path or seconds < path[0][0] or seconds > path[-1][0]:
            continue
        point = path[0]
        for left, right in zip(path, path[1:]):
            if left[0] <= seconds <= right[0]:
                ratio = (seconds - left[0]) / max(1e-9, right[0] - left[0])
                point = [seconds, left[1] + (right[1] - left[1]) * ratio, left[2] + (right[2] - left[2]) * ratio]
                break
        bounds = event.get("bounds") or {}
        boxes.append((int(point[1]), int(point[2]), int(bounds.get("width", 1)), int(bounds.get("height", 1))))
    scale_x = 270 / 1080
    scale_y = 480 / 1920
    regions = []
    for x, y, width, height in boxes:
        x0 = max(0, int((x - 4) * scale_x))
        y0 = max(0, int((y - 4) * scale_y))
        x1 = min(270, int((x + width + 4) * scale_x))
        y1 = min(480, int((y + height + 4) * scale_y))
        if x1 > x0 and y1 > y0 and (x1 - x0) * (y1 - y0) >= 32:
            regions.append((x0, y0, x1, y1))
    return regions


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--gpu", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--start", type=float, default=20)
    parser.add_argument("--end", type=float, default=35)
    parser.add_argument("--step", type=float, default=0.2)
    parser.add_argument("--fps", type=float)
    parser.add_argument("--frames", type=int)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    suspicious = []
    measurements = []
    checked = 0
    step = 1.0 / args.fps if args.fps else args.step
    frame_limit = args.frames or int(round((args.end - args.start) / step)) + 1
    source_frames = frame_stream(args.source, args.fps or (1.0 / step), frame_limit)
    gpu_frames = frame_stream(args.gpu, args.fps or (1.0 / step), frame_limit)
    total_frames = 0
    for source, gpu in zip(source_frames, gpu_frames):
        current = args.start + total_frames * step
        total_frames += 1
        regions = active_regions(plan, current)
        if regions:
            deltas = [
                float(np.percentile(np.abs(gpu[y0:y1, x0:x1] - source[y0:y1, x0:x1]), 99))
                for x0, y0, x1, y1 in regions
            ]
            delta = max(deltas)
            checked += 1
            measurements.append({"frame": total_frames - 1, "seconds": round(current, 3), "delta": delta, "regions": regions})
    signal_measurements = [item for item in measurements if item["delta"] >= 10.0]
    signal_values = [item["delta"] for item in signal_measurements]
    median_delta = float(np.median(signal_values)) if signal_values else 0.0
    threshold = max(0.5, median_delta * 0.1)
    # A flicker is an isolated disappearance between two frames that still
    # contain the overlay.  A sustained zero signal (for example an event
    # outside the visible crop) is not a one-frame flicker and is excluded.
    suspicious = []
    for index, item in enumerate(measurements[1:-1], start=1):
        previous = measurements[index - 1]["delta"]
        following = measurements[index + 1]["delta"]
        if item["delta"] < threshold and min(previous, following) >= threshold:
            suspicious.append(item)
    values = sorted(signal_values)
    p01 = values[max(0, int(len(values) * 0.01) - 1)] if values else 0.0
    print(json.dumps({"total_frames": total_frames, "checked": checked, "min_delta": values[0] if values else 0.0, "p01_delta": p01, "median_delta": median_delta, "threshold": threshold, "flicker_frames": len(suspicious), "examples": suspicious[:10]}, ensure_ascii=False))
    raise SystemExit(1 if suspicious else 0)


if __name__ == "__main__":
    main()
