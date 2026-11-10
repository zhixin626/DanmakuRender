"""Detect frames where an expected GPU overlay disappears relative to source."""

from __future__ import annotations

import argparse
import io
import json
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image


def frame(path: Path, seconds: float) -> np.ndarray:
    data = subprocess.check_output([
        "ffmpeg", "-v", "error", "-ss", f"{seconds:.3f}", "-i", str(path),
        "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-",
    ])
    return np.asarray(Image.open(io.BytesIO(data)).convert("RGB"), dtype=np.float32)


def active_bounds(plan: dict, seconds: float) -> tuple[int, int, int, int] | None:
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
    if not boxes:
        return None
    x0 = max(0, min(box[0] for box in boxes) - 4)
    y0 = max(0, min(box[1] for box in boxes) - 4)
    x1 = max(box[0] + box[2] for box in boxes) + 4
    y1 = max(box[1] + box[3] for box in boxes) + 4
    return x0, y0, x1, y1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--gpu", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--start", type=float, default=20)
    parser.add_argument("--end", type=float, default=35)
    parser.add_argument("--step", type=float, default=0.2)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    suspicious = []
    checked = 0
    current = args.start
    while current <= args.end + 1e-6:
        bounds = active_bounds(plan, current)
        if bounds:
            x0, y0, x1, y1 = bounds
            source = frame(args.source, current)[y0:y1, x0:x1]
            gpu = frame(args.gpu, current)[y0:y1, x0:x1]
            delta = float(np.abs(gpu - source).mean())
            checked += 1
            if delta < 1.5:
                suspicious.append({"seconds": round(current, 3), "delta": delta, "bounds": bounds})
        current += args.step
    print(json.dumps({"checked": checked, "flicker_frames": len(suspicious), "examples": suspicious[:10]}, ensure_ascii=False))
    raise SystemExit(1 if suspicious else 0)


if __name__ == "__main__":
    main()
