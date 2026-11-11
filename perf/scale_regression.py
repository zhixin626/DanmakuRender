"""Measure the canvas path and owned-frame allocations on isolated footage."""

from __future__ import annotations

import argparse
import importlib.util
import itertools
import json
import statistics
import subprocess
import sys
import time
import math
import hashlib
import inspect
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from DMR.Render import gpu_render as gpu
from perf.dmr_gate import decide, dmr_activity

VIDEO = next((ROOT / "perf/samples/problem_resolution").glob("*/*_clip.flv"))
EVENTS = VIDEO.with_suffix(".jsonl")
DELIVERY = ROOT / "演示视频/2026-11-11_08_缩放与持有缓冲"
WORK = ROOT / "perf/tmp/scale"


def decode_frames(module, video, nvc, cupy):
    """Call both the pre-cleanup baseline and the current explicit-CuPy API."""
    decoder = module.decode_frames
    if len(inspect.signature(decoder).parameters) >= 4:
        return decoder(video, nvc, 0, cupy)
    return decoder(video, nvc, 0)


def baseline(revision="676aa66"):
    WORK.mkdir(parents=True, exist_ok=True)
    path = WORK / "baseline.py"
    if not path.exists():
        path.write_bytes(
            subprocess.check_output(
                ["git", "show", f"{revision}:DMR/Render/gpu_render.py"], cwd=ROOT
            )
        )
    spec = importlib.util.spec_from_file_location("DMR.Render.scale_baseline", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def measure(module, iterations=600, repetitions=3):
    cupy, nvc = module.load_cuda_modules()
    frames = decode_frames(module, VIDEO, nvc, cupy)
    source = next(frames)[0]
    frames.close()
    canvas = module.NV12Buffer(cupy, 1920, 1080)
    for _ in range(10):
        canvas.load_into_canvas(source)
    cupy.cuda.get_current_stream().synchronize()
    wall, gpu_ms = [], []
    for _ in range(repetitions):
        begin, end = cupy.cuda.Event(), cupy.cuda.Event()
        start = time.perf_counter()
        begin.record()
        for _ in range(iterations):
            canvas.load_into_canvas(source)
        end.record()
        end.synchronize()
        wall.append((time.perf_counter() - start) * 1000 / iterations)
        gpu_ms.append(cupy.cuda.get_elapsed_time(begin, end) / iterations)
    constructor = module.NV12Buffer
    allocations = 0

    def counted(*args, **kwargs):
        nonlocal allocations
        allocations += 1
        return constructor(*args, **kwargs)

    module.NV12Buffer = counted
    frames = decode_frames(module, VIDEO, nvc, cupy)
    count = 0
    start = time.perf_counter()
    try:
        for _ in itertools.islice(frames, iterations):
            count += 1
    finally:
        frames.close()
        cupy.cuda.get_current_stream().synchronize()
        module.NV12Buffer = constructor
    decode_ms = (time.perf_counter() - start) * 1000 / count
    return {
        "canvas_wall_ms": wall,
        "canvas_gpu_ms": gpu_ms,
        "canvas_median_wall_ms": statistics.median(wall),
        "canvas_median_gpu_ms": statistics.median(gpu_ms),
        "decode_hold_ms": decode_ms,
        "decoded_frames": count,
        "owned_allocations": allocations,
        "source_dimensions": list(module.frame_geometry(source)),
        "canvas": [1920, 1080],
    }


def paired_timing():
    """Alternate old/new three times in one process after both kernels warm."""
    modules = {"before": baseline(), "after": gpu}
    rows = {name: [] for name in modules}
    for _ in range(3):
        for name, module in modules.items():
            if dmr_activity()["rendering"]:
                raise RuntimeError("DMR began rendering; discard timing")
            rows[name].append(measure(module, repetitions=1))
    return {
        name: {
            "trials": trials,
            "canvas_wall_ms": statistics.median(
                t["canvas_median_wall_ms"] for t in trials
            ),
            "canvas_gpu_ms": statistics.median(
                t["canvas_median_gpu_ms"] for t in trials
            ),
            "decode_hold_ms": statistics.median(t["decode_hold_ms"] for t in trials),
            "owned_allocations": [t["owned_allocations"] for t in trials],
        }
        for name, trials in rows.items()
    }


def check_sampling():
    """Compare native pitched Y/UV pixels to an independent scalar formula."""
    import numpy as np

    cupy, _ = gpu.load_cuda_modules()
    canvas = gpu.NV12Buffer(cupy, 20, 10)
    results = []
    for sw, sh in ((10, 8), (8, 10)):
        rng = np.random.default_rng(sw)
        # Wider allocations exercise actual decoder-style pitch, not just
        # contiguous owned frames. U and V have independent random values.
        host_y = rng.integers(16, 236, (sh, 16), dtype=np.uint8)
        host_uv = rng.integers(16, 241, (sh // 2, 8, 2), dtype=np.uint8)
        y = cupy.asarray(host_y)[:, :sw, None]
        uv = cupy.asarray(host_uv)[:, : sw // 2, :]

        class Frame:
            def cuda(self):
                return [y, uv]

        fw, fh, ox, oy = gpu.fit_canvas((sw, sh), (20, 10))
        for location, (phase_x, phase_y) in gpu.CHROMA_PHASES.items():
            canvas.chroma_location = location
            canvas.load_into_canvas(Frame())
            key = (sw, sh, 20, 10, location)
            table = canvas.scale_tables[key]
            canvas.load_into_canvas(Frame())
            assert canvas.scale_tables[key] is table
            expected_y = np.full((10, 20), 16, np.uint8)
            expected_uv = np.full((5, 10, 2), 128, np.uint8)
            for src, dst, w, h, xo, yo, sx, sy, tx, ty in (
                (host_y[:, :sw], expected_y, fw, fh, ox, oy, 0.5, 0.5, 0.5, 0.5),
                (
                    host_uv[:, : sw // 2],
                    expected_uv,
                    fw // 2,
                    fh // 2,
                    ox // 2,
                    oy // 2,
                    phase_x,
                    phase_y,
                    0.25,
                    0.5,
                ),
            ):
                for row in range(h):
                    source_y = min(
                        src.shape[0] - 1, max(0, (row + ty) * src.shape[0] / h - sy)
                    )
                    y0, y1 = math.floor(source_y), min(
                        math.floor(source_y) + 1, src.shape[0] - 1
                    )
                    wy = source_y - y0
                    for col in range(w):
                        source_x = min(
                            src.shape[1] - 1, max(0, (col + tx) * src.shape[1] / w - sx)
                        )
                        x0, x1 = math.floor(source_x), min(
                            math.floor(source_x) + 1, src.shape[1] - 1
                        )
                        wx = source_x - x0
                        value = (
                            src[y0, x0].astype(float) * (1 - wx) + src[y0, x1] * wx
                        ) * (1 - wy) + (
                            src[y1, x0].astype(float) * (1 - wx) + src[y1, x1] * wx
                        ) * wy
                        dst[yo + row, xo + col] = np.floor(value + 0.5)
            errors = [
                int(
                    np.abs(
                        cupy.asnumpy(actual).astype(int) - expected.astype(int)
                    ).max()
                )
                for actual, expected in (
                    (canvas.y[..., 0], expected_y),
                    (canvas.uv, expected_uv),
                )
            ]
            assert max(errors) <= 1, (sw, sh, location, errors)
            results.append(
                {
                    "source": [sw, sh],
                    "chroma": location,
                    "max_y_uv_error": errors,
                    "cache_reused": True,
                }
            )
    return results


def check_owned_sequence():
    """Compare every scheduled source frame against the immutable old decoder."""
    from perf.round_regression import VIDEO as bili

    old = baseline()
    cupy, nvc = gpu.load_cuda_modules()
    results = []
    for source in (VIDEO, bili):
        sequences = []
        for module in (old, gpu):
            frames = decode_frames(module, source, nvc, cupy)
            first = next(frames)
            origin = float(
                module.FFprobe.run_ffprobe(str(source))["format"].get("start_time", 0)
            )
            source_fps = module.source_frame_rate(str(source))
            sequence = []
            try:
                for owned, seconds in module.schedule_frames(
                    frames,
                    first,
                    50,
                    source_fps=source_fps,
                    start_offset=max(0, first[1] - origin),
                ):
                    digest = hashlib.sha256()
                    for plane in (owned.y, owned.uv):
                        digest.update(cupy.asnumpy(plane[::32, ::32]).tobytes())
                    sequence.append(
                        (
                            round(seconds, 6),
                            module.frame_geometry(owned),
                            digest.hexdigest(),
                        )
                    )
            finally:
                frames.close()
            sequences.append(sequence)
        assert sequences[0] == sequences[1], next(
            (i for i, (a, b) in enumerate(zip(*sequences)) if a != b), "length mismatch"
        )
        results.append(
            {
                "source": str(source.relative_to(ROOT)),
                "scheduled_frames": len(sequences[0]),
                "y_uv_signature_mismatches": 0,
                "sampling": "Y/UV every 32 pixels; every output frame and PTS",
            }
        )
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--paired", action="store_true")
    parser.add_argument("--sequence", action="store_true")
    args = parser.parse_args()
    gate = decide(5)
    if not gate["go"]:
        print(json.dumps(gate, ensure_ascii=False))
        return 10
    DELIVERY.mkdir(parents=True, exist_ok=True)
    if args.check:
        result = check_sampling()
        DELIVERY.mkdir(parents=True, exist_ok=True)
        (DELIVERY / "sampling.json").write_text(
            json.dumps(result, indent=2), encoding="utf8"
        )
        print(json.dumps(result))
        return 0
    if args.sequence:
        result = check_owned_sequence()
        if dmr_activity()["rendering"]:
            raise RuntimeError("DMR began rendering; discard check")
        (DELIVERY / "sequence.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8"
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.paired:
        result = paired_timing()
        if dmr_activity()["rendering"]:
            raise RuntimeError("DMR began rendering; discard timing")
        (DELIVERY / "timing.json").write_text(
            json.dumps({"gate": gate, **result}, ensure_ascii=False, indent=2),
            encoding="utf8",
        )
        print(
            json.dumps(
                {
                    name: {k: v for k, v in row.items() if k != "trials"}
                    for name, row in result.items()
                }
            )
        )
        return 0
    module = baseline() if args.baseline else gpu
    result = measure(module)
    if dmr_activity()["rendering"]:
        raise RuntimeError("DMR began rendering; discard timing")
    DELIVERY.mkdir(parents=True, exist_ok=True)
    path = DELIVERY / ("before.json" if args.baseline else "after.json")
    path.write_text(
        json.dumps({"gate": gate, **result}, ensure_ascii=False, indent=2),
        encoding="utf8",
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
