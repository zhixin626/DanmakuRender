"""Resolution decisions shared by layout compilation and render backends."""

from __future__ import annotations


LANDSCAPE_1080P = (1920, 1080)
PORTRAIT_1080P = (1080, 1920)
RESCALE_ORIENTATIONS = {"auto", "adaptive", "portrait", "landscape"}
STRONG_ASPECT_RATIO = 1.5


def normalize_rescale_orientation(value) -> str:
    orientation = str(value or "auto").strip().lower()
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
