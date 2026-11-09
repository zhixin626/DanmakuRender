"""Shared media probing helpers used by all render engines."""

from __future__ import annotations

import json
import subprocess

import math


def resolve_output_fps(source_fps: float, minimum_fps: float | None = None) -> float:
    """Keep the source rate unless a configured minimum is higher."""
    source_fps = float(source_fps)
    if not math.isfinite(source_fps) or source_fps <= 0:
        raise ValueError("source fps must be a positive number")
    if minimum_fps is None:
        return source_fps
    minimum_fps = float(minimum_fps)
    if not math.isfinite(minimum_fps) or minimum_fps <= 0:
        raise ValueError("minimum fps must be a positive number")
    return max(source_fps, minimum_fps)


def source_frame_rate(video_path: str) -> float:
    """Use PyAV's guessed rate, then average rate, like the old renderer."""
    try:
        import av
    except ModuleNotFoundError:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=avg_frame_rate", "-of", "json", video_path],
            check=True,
            capture_output=True,
            text=True,
        )
        rate = json.loads(result.stdout)["streams"][0]["avg_frame_rate"]
        numerator, denominator = rate.split("/")
        return float(numerator) / float(denominator)
    container = av.open(video_path)
    try:
        stream = container.streams.video[0]
        return float(stream.guessed_rate or stream.average_rate)
    finally:
        container.close()
