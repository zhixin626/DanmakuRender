"""Fixed-shape RenderPlan records used by tests and atlas boundaries."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RenderEvent:
    """Fixed-shape event record loaded from the generated GPU atlas."""

    id: str | None
    offset: int
    width: int
    height: int
    start: float
    end: float
    path: tuple[tuple[float, float, float], ...]
    motion: dict[str, Any]

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "RenderEvent":
        required = ("offset", "width", "height", "start", "end", "path", "motion")
        for key in required:
            if key not in value:
                raise KeyError(key)
        return cls(
            id=value.get("id"),
            offset=int(value["offset"]),
            width=int(value["width"]),
            height=int(value["height"]),
            start=float(value["start"]),
            end=float(value["end"]),
            path=tuple(tuple(float(part) for part in point) for point in value["path"]),
            motion=dict(value["motion"]),
        )


@dataclass(frozen=True)
class PanelNode:
    type: str
    x: int
    y: int
    width: int
    height: int
    shape: str | None
    font: str | None
    size: float | None

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "PanelNode":
        required = ("type", "x", "y", "width", "height")
        for key in required:
            if key not in value:
                raise KeyError(key)
        return cls(
            type=str(value["type"]), x=int(value["x"]), y=int(value["y"]),
            width=int(value["width"]), height=int(value["height"]),
            shape=value.get("shape"), font=value.get("font"), size=value.get("size"),
        )
