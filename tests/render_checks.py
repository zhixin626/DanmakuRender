"""Measure each expected sprite separately; surviving text must not mask loss."""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
from PIL import Image

from DMR.Render.gpu_render import EventScanner
from DMR.Render.layout_models import RenderEvent


class OverlaySignal:
    """Project RGB output/source differences onto each sprite's expected signal."""

    def __init__(self, atlas, canvas, size):
        self.scale = (size[0] / canvas[0], size[1] / canvas[1])
        self.size = size
        self.scanner = EventScanner(
            [RenderEvent.from_mapping(e) for e in atlas["events"]]
        )
        self.sprites = {}
        for e in self.scanner.events:
            shape = (e.height, e.width)
            start, end = e.offset, e.offset + e.width * e.height
            y, u, v, a = [
                atlas[k][start:end].reshape(shape).astype(np.float32)
                for k in ("y", "u", "v", "a")
            ]
            y = (y - 16) / 0.8588
            u, v = u - 128, v - 128
            rgb = np.clip(
                np.stack(
                    (y + 1.7927 * v, y - 0.2132 * u - 0.5329 * v, y + 2.1124 * u),
                    axis=2,
                ),
                0,
                255,
            )
            rgba = np.dstack((rgb, a)).astype(np.uint8)
            target = (
                max(1, round(e.width * self.scale[0])),
                max(1, round(e.height * self.scale[1])),
            )
            # Premultiplied resizing preserves text edge alpha at inspection scale.
            sprite = (
                Image.fromarray(rgba)
                .convert("RGBa")
                .resize(target, Image.Resampling.BILINEAR)
                .convert("RGBA")
            )
            self.sprites[e.id] = np.asarray(sprite).astype(np.float32)

    def measure(self, source, output, seconds):
        # Reconstruct the engine's track order, including scrolling text above
        # all panels, and inspect each layer's final visible contribution.
        expected = source.copy()
        layers = []
        for event, x, y, fade in self.scanner.visible(seconds):
            sprite = self.sprites[event.id]
            x, y = round(x * self.scale[0]), round(y * self.scale[1])
            h, w = sprite.shape[:2]
            x0, y0, x1, y1 = (
                max(0, x),
                max(0, y),
                min(self.size[0], x + w),
                min(self.size[1], y + h),
            )
            if x1 <= x0 or y1 <= y0:
                continue
            crop = sprite[y0 - y : y1 - y, x0 - x : x1 - x]
            alpha = crop[..., 3:4] / 255 * (fade / 255)
            region = expected[y0:y1, x0:x1]
            contribution = (crop[..., :3] - region) * alpha
            region += contribution
            eligible = (
                fade >= 245
                and seconds - event.start >= 0.3
                and event.end - seconds >= 0.3
                and (x1 - x0) * (y1 - y0) >= 0.6 * w * h
            )
            layers.append((event, (x0, y0, x1, y1), alpha, contribution, eligible))
        transmission = np.ones((*source.shape[:2], 1), np.float32)
        measurements = []
        for event, (x0, y0, x1, y1), alpha, contribution, eligible in reversed(layers):
            above = transmission[y0:y1, x0:x1]
            unoccluded = above[..., 0] > 0.9
            signal = contribution * above
            above *= 1 - alpha
            if not eligible:
                continue
            # A translucent ghost behind another panel is too weak for a
            # disappearance decision; inspect substantial unoccluded support.
            mask = (
                unoccluded
                & (alpha[..., 0] > 0.27)
                & (np.max(np.abs(signal), axis=2) > 25)
            )
            if mask.sum() < 24:
                continue
            template = signal[mask]
            energy = np.sum(template * template)
            scores = []
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if (
                        x0 + dx < 0
                        or y0 + dy < 0
                        or x1 + dx > self.size[0]
                        or y1 + dy > self.size[1]
                    ):
                        continue
                    residual = (
                        output[y0 + dy : y1 + dy, x0 + dx : x1 + dx]
                        - expected[y0:y1, x0:x1]
                    )
                    scores.append(
                        float(np.sum((template + residual[mask]) * template) / energy)
                    )
            measurements.append(
                {
                    "event": event.id,
                    "score": round(max(scores), 4),
                    "pixels": int(mask.sum()),
                }
            )
        return measurements


def find_dropouts(measurements, threshold=0.2):
    """Flag an 80% signal loss next to visible frames, including short runs."""
    by_event = {}
    for frame in measurements:
        for item in frame["signals"]:
            by_event.setdefault(item["event"], []).append(
                {**item, "frame": frame["frame"], "seconds": frame["seconds"]}
            )
    suspicious = []
    for event, values in by_event.items():
        index = 0
        while index < len(values):
            if values[index]["score"] >= threshold:
                index += 1
                continue
            start = index
            index += 1
            while (
                index < len(values)
                and values[index]["score"] < threshold
                and values[index]["frame"] == values[index - 1]["frame"] + 1
            ):
                index += 1
            before = values[max(0, start - 5) : start]
            after = values[index : index + 5]
            anchored = any(
                n["score"] > 0.65 and values[start]["frame"] - n["frame"] <= 5
                for n in before
            )
            anchored |= any(
                n["score"] > 0.65 and n["frame"] - values[index - 1]["frame"] <= 5
                for n in after
            )
            if anchored:
                suspicious.extend(values[start:index])
    return sorted(suspicious, key=lambda item: (item["frame"], item["event"]))


def frame_stream(
    path: Path, fps: float, frames: int, start: float = 0, size=(270, 480), indices=None
):
    """Decode a bounded stream once instead of spawning ffmpeg per frame."""
    width, height = size
    # Match the renderer's common audio/video origin: source video can start
    # after audio, so explicitly retain its first frame back to grid time zero.
    filters = [f"fps={fps}:start_time=0", f"trim=start={start}", "setpts=PTS-STARTPTS"]
    if indices is not None:
        expression = "+".join(f"eq(n,{index})" for index in indices)
        filters.append(f"select='{expression}'")
    filters += [
        f"scale={width}:{height}:force_original_aspect_ratio=decrease",
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
        "format=rgb24",
    ]
    process = subprocess.Popen(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-frames:v",
            str(frames),
            "-vf",
            ",".join(filters),
            "-fps_mode",
            "passthrough",
            "-f",
            "rawvideo",
            "-",
        ],
        stdout=subprocess.PIPE,
    )
    size = width * height * 3
    last = None
    try:
        for _ in range(frames):
            data = process.stdout.read(size)
            if len(data) != size:
                break
            last = (
                np.frombuffer(data, dtype=np.uint8)
                .reshape(height, width, 3)
                .astype(np.float32)
            )
            yield last
    finally:
        if process.poll() is None:
            process.terminate()
        process.stdout.close()
        process.wait()
