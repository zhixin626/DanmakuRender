"""Detect frames where an expected GPU overlay disappears relative to source."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import numpy as np
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.render_checks import OverlaySignal, find_dropouts, frame_stream
from DMR.Render.sprites import build_atlas
from DMR.utils import FFprobe


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
    parser.add_argument("--canvas")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    measurements = []
    step = 1.0 / args.fps if args.fps else args.step
    frame_limit = args.frames or int(round((args.end - args.start) / step)) + 1
    if step <= 0 or frame_limit <= 0:
        parser.error("检查帧率、步长和帧数必须为正数")
    canvas = (
        tuple(int(v) for v in args.canvas.split("x"))
        if args.canvas
        else FFprobe.get_resolution(str(args.gpu))
    )
    size = (round(canvas[0] / 4), round(canvas[1] / 4))
    detector = OverlaySignal(build_atlas(plan), canvas, size)
    source_frames = frame_stream(
        args.source, args.fps or (1.0 / step), frame_limit, args.start, size
    )
    gpu_frames = frame_stream(
        args.gpu, args.fps or (1.0 / step), frame_limit, args.start, size
    )
    total_frames = 0
    try:
        for source, gpu in zip(source_frames, gpu_frames):
            current = args.start + total_frames * step
            total_frames += 1
            measurements.append(
                {
                    "frame": total_frames - 1,
                    "seconds": round(current, 4),
                    "signals": detector.measure(source, gpu, current),
                }
            )
    finally:
        source_frames.close()
        gpu_frames.close()
    suspicious = find_dropouts(measurements)
    candidates = len({item["frame"] for item in suspicious})
    if suspicious:
        # Quarter-size text edges can alias on a pale background. Verify only
        # candidates at native resolution, retaining the same threshold; never
        # lower the threshold just to make a rendered video pass.
        indices = sorted({item["frame"] for item in suspicious})
        selected = {(item["frame"], item["event"]) for item in suspicious}
        native = OverlaySignal(build_atlas(plan), canvas, canvas)
        source_native = frame_stream(
            args.source, 1 / step, len(indices), args.start, canvas, indices
        )
        gpu_native = frame_stream(
            args.gpu, 1 / step, len(indices), args.start, canvas, indices
        )
        verified = []
        native_count = 0
        try:
            for index, (source, gpu) in zip(indices, zip(source_native, gpu_native)):
                seconds = args.start + index * step
                native_count += 1
                for item in native.measure(source, gpu, seconds):
                    if (index, item["event"]) in selected and item["score"] < 0.2:
                        verified.append(
                            {**item, "frame": index, "seconds": round(seconds, 4)}
                        )
        finally:
            source_native.close()
            gpu_native.close()
        if native_count != len(indices):
            raise RuntimeError("原分辨率候选帧检查不完整")
        suspicious = verified
    report = {
        "total_frames": total_frames,
        "requested_frames": frame_limit,
        "complete": total_frames == frame_limit,
        "checked": sum(bool(item["signals"]) for item in measurements),
        "candidate_frames": candidates,
        "flicker_frames": len({item["frame"] for item in suspicious}),
        "missing_regions": len(suspicious),
        "examples": suspicious[:20],
    }
    if args.report:
        args.report.write_text(
            json.dumps({**report, "measurements": measurements}, ensure_ascii=False),
            encoding="utf8",
        )
    print(json.dumps(report, ensure_ascii=False))
    raise SystemExit(2 if total_frames != frame_limit else (1 if suspicious else 0))


if __name__ == "__main__":
    main()
