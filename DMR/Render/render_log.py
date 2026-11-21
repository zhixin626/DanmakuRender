"""Helpers for the one-block start/end render summaries."""

from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar

# Problems noted while one render runs, summed up in its end summary.
_ISSUES: ContextVar[Counter | None] = ContextVar("dmr_render_issues", default=None)


def format_hms(seconds: float) -> str:
    """1小时2分3秒 style; leading zero units are omitted."""
    total = max(0, int(round(float(seconds))))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}小时{minutes}分{secs}秒"
    if minutes:
        return f"{minutes}分{secs}秒"
    return f"{secs}秒"


def format_size(num_bytes: float) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB"):
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.2f} GB"


def note_issue(kind: str) -> None:
    counter = _ISSUES.get()
    if counter is not None:
        counter[kind] += 1


@contextmanager
def collect_issues():
    counter: Counter = Counter()
    token = _ISSUES.set(counter)
    try:
        yield counter
    finally:
        _ISSUES.reset(token)
