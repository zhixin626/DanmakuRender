from __future__ import annotations

import copy
import json
import os
import tempfile
from dataclasses import dataclass, field


SCHEMA_VERSION = 1


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

    def to_dict(self) -> dict:
        return {
            "schema": SCHEMA_VERSION,
            "at": float(self.at),
            "events": copy.deepcopy(self.events),
        }

    @classmethod
    def from_dict(cls, data: dict | None, default_at: float = 0.0) -> "LayoutState":
        if not data:
            return cls(float(default_at), [])
        if int(data.get("schema", 0)) != SCHEMA_VERSION:
            raise ValueError(f"unsupported LayoutState schema: {data.get('schema')}")
        return cls(
            at=float(data.get("at", default_at)),
            events=copy.deepcopy(data.get("events") or []),
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
            "schema": SCHEMA_VERSION,
            "duration": float(self.duration),
            "events": copy.deepcopy(self.events),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RenderPlan":
        if int(data.get("schema", 0)) != SCHEMA_VERSION:
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
