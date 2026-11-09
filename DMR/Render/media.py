"""Shared media probing helpers used by all render engines."""

from __future__ import annotations

import math


def resolve_output_fps(source_fps: float, minimum_fps: float | None = None) -> float:
    """Return the smallest integer multiple of the source rate meeting minimum."""
    source_fps = float(source_fps)
    if not math.isfinite(source_fps) or source_fps <= 0:
        raise ValueError("source fps must be a positive number")
    if minimum_fps is None:
        return source_fps
    minimum_fps = float(minimum_fps)
    if not math.isfinite(minimum_fps) or minimum_fps <= 0:
        raise ValueError("minimum fps must be a positive number")
    multiplier = max(1, math.ceil(minimum_fps / source_fps - 1e-9))
    return source_fps * multiplier


def source_frame_rate(video_path: str) -> float:
    """Use PyAV's guessed rate, then average rate, like the old renderer."""
    import av

    container = av.open(video_path)
    try:
        stream = container.streams.video[0]
        return float(stream.guessed_rate or stream.average_rate)
    finally:
        container.close()
