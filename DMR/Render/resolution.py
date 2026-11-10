"""Resolution decisions shared by layout compilation and render backends."""

from __future__ import annotations

import math
from dataclasses import dataclass

LANDSCAPE_1080P = (1920, 1080)
PORTRAIT_1080P = (1080, 1920)
RESCALE_ORIENTATIONS = {"auto", "portrait", "landscape"}
STRONG_ASPECT_RATIO = 1.5


@dataclass(frozen=True)
class ResolutionSpan:
    """A stable source resolution over one media-time interval."""

    start: float
    end: float
    width: int
    height: int

    @property
    def duration(self) -> float:
        return max(0.0, float(self.end) - float(self.start))

    @property
    def orientation(self) -> str:
        return _orientation((self.width, self.height))


def normalize_resolution_spans(
    spans, *, ignore_head_seconds: float = 30.0, min_span_seconds: float = 60.0
) -> list[ResolutionSpan]:
    """Drop startup noise and short switches from a keyframe timeline."""
    result = []
    for raw in spans:
        if isinstance(raw, ResolutionSpan):
            span = raw
        else:
            start, end, width, height = raw
            span = ResolutionSpan(float(start), float(end), int(width), int(height))
        start = max(span.start, float(ignore_head_seconds))
        filtered = ResolutionSpan(start, span.end, span.width, span.height)
        if filtered.duration >= float(min_span_seconds):
            result.append(filtered)
    return result


def choose_dominant_orientation(spans) -> str:
    """Return the orientation with the greatest stable duration."""
    totals = {"portrait": 0.0, "landscape": 0.0}
    for span in spans:
        totals[span.orientation] += span.duration
    if not any(totals.values()):
        raise ValueError("resolution timeline has no stable spans")
    return max(totals, key=totals.get)


def decide_session_parts(
    spans,
    *,
    ignore_head_seconds: float = 30.0,
    min_span_seconds: float = 60.0,
    minority_ratio: float = 0.15,
    minority_minutes: float = 10.0,
    forced_orientation: str = "auto",
) -> dict:
    """Choose one normalized video or consecutive orientation parts."""
    policy = normalize_rescale_orientation(forced_orientation)
    stable = normalize_resolution_spans(
        spans,
        ignore_head_seconds=ignore_head_seconds,
        min_span_seconds=min_span_seconds,
    )
    if not stable:
        raise ValueError("resolution timeline has no spans")
    dominant = policy if policy in {"portrait", "landscape"} else choose_dominant_orientation(stable)
    totals = {"portrait": 0.0, "landscape": 0.0}
    for span in stable:
        totals[span.orientation] += span.duration
    total = sum(totals.values())
    minority = total - totals[dominant]
    if minority <= 0:
        action = "single"
    elif minority / total < float(minority_ratio) or minority < float(minority_minutes) * 60:
        action = "re_render_minority"
    else:
        action = "parts"
    runs = []
    for span in stable:
        if runs and runs[-1]["orientation"] == span.orientation:
            runs[-1]["end"] = span.end
        else:
            runs.append({"orientation": span.orientation, "start": span.start, "end": span.end})
    return {
        "dominant": dominant,
        "action": action,
        "total_seconds": total,
        "minority_seconds": minority,
        "runs": runs,
        "canvas": _canvas(dominant),
    }


def normalize_rescale_orientation(value) -> str:
    orientation = str(value or "auto").strip().lower()
    if orientation == "adaptive":
        orientation = "auto"
    if orientation not in RESCALE_ORIENTATIONS:
        choices = ", ".join(sorted(RESCALE_ORIENTATIONS))
        raise ValueError(f"rescale_orientation must be one of: {choices}")
    return orientation


def _resolution(value) -> tuple[int, int] | None:
    if not value or len(value) < 2:
        return None
    width, height = int(value[0] or 0), int(value[1] or 0)
    return (width, height) if width > 0 and height > 0 else None


def _orientation(resolution: tuple[int, int]) -> str:
    return "landscape" if resolution[0] >= resolution[1] else "portrait"


def _is_strong(resolution: tuple[int, int] | None) -> bool:
    if not resolution:
        return False
    return max(resolution) / min(resolution) >= STRONG_ASPECT_RATIO


def _canvas(orientation: str) -> tuple[int, int]:
    return PORTRAIT_1080P if orientation == "portrait" else LANDSCAPE_1080P


def resolve_render_canvas(
    resolution,
    *,
    rescale_to_1080p: bool = False,
    rescale_orientation: str = "auto",
    previous_canvas=None,
    previous_source_resolution=None,
) -> tuple[int, int]:
    """Return the layout/render canvas for a source resolution."""
    source = _resolution(resolution) or LANDSCAPE_1080P

    if not rescale_to_1080p:
        return source

    policy = normalize_rescale_orientation(rescale_orientation)
    if policy in {"portrait", "landscape"}:
        return _canvas(policy)

    current_orientation = _orientation(source)
    if policy == "auto":
        return _canvas(current_orientation)

    previous = _resolution(previous_canvas)
    previous_source = _resolution(previous_source_resolution)
    if not previous:
        return _canvas(current_orientation)

    previous_orientation = _orientation(previous)
    if current_orientation == previous_orientation or _is_strong(source):
        return _canvas(current_orientation)
    if _is_strong(previous_source):
        return _canvas(previous_orientation)
    return _canvas(current_orientation)


def fit_canvas(source, canvas) -> tuple[int, int, int, int]:
    """Return NV12-safe fitted width, height and centered offsets."""
    sw, sh = source
    cw, ch = canvas
    if min(sw, sh, cw, ch) <= 0 or cw % 2 or ch % 2:
        raise ValueError("NV12 canvas must have positive even dimensions")
    scale = min(cw / sw, ch / sh)
    width = max(2, min(cw, round(sw * scale) // 2 * 2))
    height = max(2, min(ch, round(sh * scale) // 2 * 2))
    return width, height, (cw - width) // 4 * 2, (ch - height) // 4 * 2


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
