from __future__ import annotations

import hashlib
import os
import threading
from collections import defaultdict

from .layout_compiler import LayoutCompiler, LayoutCompileResult
from .layout_models import LayoutState


class LayoutTrackManager:
    """Own per-live-task layout checkpoints and serialize segment compilation."""

    def __init__(self, logger=None):
        self.logger = logger
        self._condition = threading.Condition(threading.RLock())
        self._states: dict[str, LayoutState] = {}
        self._history: dict[str, dict[int, LayoutState | None]] = defaultdict(dict)
        self._next_sequence = defaultdict(int)
        self._state_paths: dict[str, str] = {}
        self._closed: set[str] = set()

    def begin(self, track_key: str) -> None:
        """Open a fresh live track, including reuse of a previously closed key."""
        with self._condition:
            self._closed.discard(track_key)
            self._states.pop(track_key, None)
            self._history.pop(track_key, None)
            self._next_sequence[track_key] = 0
            self._condition.notify_all()

    def compile_segment(
        self,
        *,
        track_key: str,
        sequence: int,
        video,
        events_path: str,
        layout_config: dict,
        engine: str,
        engine_config: dict,
        work_dir: str | None,
    ) -> LayoutCompileResult:
        if track_key is None or sequence is None:
            raise ValueError("JSONL 渲染任务缺少 track 标识或分段序号")
        with self._condition:
            while sequence > self._next_sequence[track_key]:
                if track_key in self._closed:
                    raise RuntimeError(f"track {track_key} 已关闭")
                self._condition.wait()
            if track_key in self._closed:
                raise RuntimeError(f"track {track_key} 已关闭")
            retry = sequence < self._next_sequence[track_key]
            if retry and sequence not in self._history.get(track_key, {}):
                raise RuntimeError(
                    f"track {track_key} 的分段 {sequence} 已经编译，但缺少重试检查点"
                )
            previous = (
                self._history[track_key][sequence]
                if retry
                else self._states.get(track_key)
            )
            if previous is not None:
                previous = LayoutState.from_dict(previous.to_dict())

        result = LayoutCompiler(
            layout_config,
            engine=engine,
            engine_config=engine_config,
        ).compile(
            video=video,
            events_path=events_path,
            previous_state=previous,
            work_dir=work_dir,
        )
        with self._condition:
            if track_key in self._closed:
                result.cleanup()
                raise RuntimeError(f"track {track_key} 在布局编译期间被关闭")
        if retry:
            # Rebuild the historical segment from the exact checkpoint it used
            # originally, without rewinding or consuming the current live state.
            return result

        state_path = self._state_path(track_key, work_dir)
        if result.next_state.events:
            result.next_state.save(state_path)
        else:
            try:
                if os.path.exists(state_path):
                    os.remove(state_path)
            except OSError:
                pass
        with self._condition:
            self._history[track_key][sequence] = (
                LayoutState.from_dict(previous.to_dict())
                if previous is not None
                else None
            )
            self._states[track_key] = result.next_state
            if result.next_state.events:
                self._state_paths[track_key] = state_path
            else:
                self._state_paths.pop(track_key, None)
            if sequence == self._next_sequence[track_key]:
                self._next_sequence[track_key] += 1
            self._condition.notify_all()
        return result

    def reset(self, track_key: str | None = None) -> None:
        with self._condition:
            keys = (
                [track_key]
                if track_key is not None
                else list(
                    set(self._state_paths)
                    | set(self._states)
                    | set(self._history)
                    | set(self._next_sequence)
                )
            )
            paths = [self._state_paths.pop(key, None) for key in keys]
            for key in keys:
                self._closed.add(key)
                self._states.pop(key, None)
                self._history.pop(key, None)
                self._next_sequence.pop(key, None)
            self._condition.notify_all()
        for path in paths:
            try:
                if path and os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass

    @staticmethod
    def _state_path(track_key: str, work_dir: str | None) -> str:
        digest = hashlib.sha1(track_key.encode("utf-8")).hexdigest()[:12]
        directory = work_dir or "."
        os.makedirs(directory, exist_ok=True)
        return os.path.join(directory, f".layout_state_{digest}.json")
