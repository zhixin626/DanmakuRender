from __future__ import annotations

import copy
import json
import os
import tempfile
from dataclasses import dataclass, field
from typing import Any


LAYOUT_STATE_SCHEMA_VERSION = 3
RENDER_PLAN_SCHEMA_VERSION = 1


def _atomic_json_dump(path: str, data: dict) -> None:
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".layout_", suffix=".tmp", dir=directory)
    os.close(fd)
    try:
        with open(temp_path, "w", encoding="utf-8", newline="\n") as fp:
            json.dump(data, fp, ensure_ascii=False, separators=(",", ":"))
        os.replace(temp_path, path)
    finally:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass


@dataclass
class LayoutState:
    """A checkpoint at the end of a recorded video segment.

    State paths use absolute wall-clock seconds.  Only events whose path extends
    beyond ``at`` are retained.
    """

    at: float
    events: list[dict] = field(default_factory=list)
    # Canonical bottom-to-top event IDs for each stacked panel track.
    # Keeping the order as a list avoids duplicate or sparse numeric ranks.
    track_orders: dict[str, list[str]] = field(default_factory=dict)
    canvas_resolution: tuple[int, int] | None = None
    source_resolution: tuple[int, int] | None = None

    def to_dict(self) -> dict:
        return {
            "schema": LAYOUT_STATE_SCHEMA_VERSION,
            "at": float(self.at),
            "events": copy.deepcopy(self.events),
            "track_orders": copy.deepcopy(self.track_orders),
            "canvas_resolution": (
                list(self.canvas_resolution)
                if self.canvas_resolution is not None
                else None
            ),
            "source_resolution": (
                list(self.source_resolution)
                if self.source_resolution is not None
                else None
            ),
        }

    @classmethod
    def from_dict(cls, data: dict | None, default_at: float = 0.0) -> "LayoutState":
        if not data:
            return cls(float(default_at), [])
        if int(data.get("schema", 0)) != LAYOUT_STATE_SCHEMA_VERSION:
            raise ValueError(f"unsupported LayoutState schema: {data.get('schema')}")
        def parse_resolution(key):
            value = data.get(key)
            if isinstance(value, (list, tuple)) and len(value) >= 2:
                return int(value[0]), int(value[1])
            return None

        return cls(
            at=float(data.get("at", default_at)),
            events=copy.deepcopy(data.get("events") or []),
            track_orders={
                str(track): [str(event_id) for event_id in (order or [])]
                for track, order in (data.get("track_orders") or {}).items()
            },
            canvas_resolution=parse_resolution("canvas_resolution"),
            source_resolution=parse_resolution("source_resolution"),
        )

    @classmethod
    def load(cls, path: str, default_at: float = 0.0) -> "LayoutState":
        if not path or not os.path.exists(path):
            return cls(float(default_at), [])
        with open(path, "r", encoding="utf-8") as fp:
            return cls.from_dict(json.load(fp), default_at=default_at)

    def save(self, path: str) -> None:
        _atomic_json_dump(path, self.to_dict())


@dataclass
class RenderPlan:
    """Backend-neutral drawing instructions for exactly one video segment."""

    duration: float
    events: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "schema": RENDER_PLAN_SCHEMA_VERSION,
            "duration": float(self.duration),
            "events": copy.deepcopy(self.events),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RenderPlan":
        if int(data.get("schema", 0)) != RENDER_PLAN_SCHEMA_VERSION:
            raise ValueError(f"unsupported RenderPlan schema: {data.get('schema')}")
        return cls(
            duration=float(data.get("duration", 0.0)),
            events=copy.deepcopy(data.get("events") or []),
        )

    @classmethod
    def load(cls, path: str) -> "RenderPlan":
        with open(path, "r", encoding="utf-8") as fp:
            return cls.from_dict(json.load(fp))

    def save(self, path: str) -> None:
        _atomic_json_dump(path, self.to_dict())


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
