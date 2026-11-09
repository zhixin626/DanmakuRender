"""Compare E/G output quality and configuration sensitivity for 1b-2."""

from __future__ import annotations

import argparse
import io
import json
import math
import pathlib
import subprocess

import numpy as np
from PIL import Image


def frame(path: pathlib.Path, seconds: float) -> np.ndarray:
    """Decode one RGB frame at a timestamp without writing outside perf."""
    command = [
        "ffmpeg", "-v", "error", "-ss", f"{seconds:.3f}", "-i", str(path),
        "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-",
    ]
    data = subprocess.check_output(command)
    return np.asarray(Image.open(io.BytesIO(data)).convert("RGB"), dtype=np.float32)


def position(path: list[list[float]], seconds: float) -> tuple[float, float] | None:
    """Interpolate an event path at a timestamp."""
    if not path or seconds < path[0][0] or seconds > path[-1][0]:
        return None
    for left, right in zip(path, path[1:]):
        if left[0] <= seconds <= right[0]:
            span = right[0] - left[0]
            ratio = 0.0 if span <= 0 else (seconds - left[0]) / span
            return (
                left[1] + (right[1] - left[1]) * ratio,
                left[2] + (right[2] - left[2]) * ratio,
            )
    return float(path[-1][1]), float(path[-1][2])


def no_event_mask(plan: dict, seconds: float, shape: tuple[int, int]) -> np.ndarray:
    """Mask pixels outside active RenderPlan bounds for a no-danmaku PSNR."""
    height, width = shape
    active = np.zeros((height, width), dtype=bool)
    for event in plan.get("events", []):
        point = position(event.get("path") or [], seconds)
        if point is None:
            continue
        bounds = event.get("bounds") or {}
        event_width = int(bounds.get("width", 0))
        event_height = int(bounds.get("height", 0))
        x = int(round(point[0]))
        y = int(round(point[1]))
        x0 = max(0, x - 4)
        y0 = max(0, y - 4)
        x1 = min(width, x + event_width + 4)
        y1 = min(height, y + event_height + 4)
        if x1 > x0 and y1 > y0:
            active[y0:y1, x0:x1] = True
    return ~active


def psnr(first: np.ndarray, second: np.ndarray, mask: np.ndarray) -> float:
    """Return PSNR in dB for selected pixels."""
    difference = first[mask] - second[mask]
    mse = float(np.mean(np.square(difference))) if difference.size else 0.0
    return 99.0 if mse <= 1e-12 else 10.0 * math.log10((255.0 ** 2) / mse)


def heatmap(first: np.ndarray, second: np.ndarray, output: pathlib.Path) -> None:
    """Write a red-blue absolute-difference heatmap."""
    difference = np.abs(first - second).mean(axis=2)
    scaled = np.clip(difference * 8.0, 0, 255).astype(np.uint8)
    image = np.zeros((*scaled.shape, 3), dtype=np.uint8)
    image[:, :, 0] = scaled
    image[:, :, 2] = 255 - scaled
    Image.fromarray(image).save(output)


def main() -> None:
    """Compare baseline E/G and configurable variant B."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--e", type=pathlib.Path, required=True)
    parser.add_argument("--g", type=pathlib.Path, required=True)
    parser.add_argument("--e-variant", type=pathlib.Path, required=True)
    parser.add_argument("--g-variant", type=pathlib.Path, required=True)
    parser.add_argument("--plan", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--g-offset", type=float, default=-0.16)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    times = (30.0, 90.0, 150.0, 210.0, 270.0)
    rows = []
    for seconds in times:
        baseline_e = frame(args.e, seconds)
        baseline_g = frame(args.g, seconds + args.g_offset)
        variant_e = frame(args.e_variant, seconds)
        variant_g = frame(args.g_variant, seconds + args.g_offset)
        mask = no_event_mask(plan, seconds, baseline_e.shape[:2])
        rows.append({
            "seconds": seconds,
            "baseline_psnr_db_no_event": psnr(baseline_e, baseline_g, mask),
            "baseline_mad": float(np.abs(baseline_e - baseline_g).mean()),
            "config_change_mad_e": float(np.abs(baseline_e - variant_e).mean()),
            "config_change_mad_g": float(np.abs(baseline_g - variant_g).mean()),
            "variant_psnr_db_no_event": psnr(variant_e, variant_g, mask),
        })
        if seconds == 150.0:
            heatmap(baseline_e, baseline_g, args.output / "baseline_E_vs_G_heatmap.png")
            heatmap(variant_e, variant_g, args.output / "variant_E_vs_G_heatmap.png")
    result = {"times": rows, "baseline_mean_psnr_db": float(np.mean([row["baseline_psnr_db_no_event"] for row in rows])), "variant_mean_psnr_db": float(np.mean([row["variant_psnr_db_no_event"] for row in rows]))}
    (args.output / "comparison.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
