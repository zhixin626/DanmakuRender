"""Decide whether it is a good moment to run performance tests.

The user's PC runs DMR (live recording + rendering) around the clock. Tests
must never compete with a real DMR render, and should preferably run while
DMR is completely idle. This script looks at the running processes, applies
the policy from perf/PLAN.md ("测试时机"), prints one JSON line and exits with:

    0   -> go: start the test now
    10  -> wait: check again later

It never starts, stops or touches any DMR process. Usage:

    python perf/dmr_gate.py            # decide (and remember when waiting began)
    python perf/dmr_gate.py --reset    # forget the waiting start time (after a test run)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys
import time

import psutil

PERF_DIR = pathlib.Path(__file__).resolve().parent
WAIT_SINCE_FILE = PERF_DIR / "tmp" / "gate_wait_since.txt"
LOG_FILE = PERF_DIR / "tmp" / "gate.log"

# Hours (local time) in which the PC is usually idle. Only used to pick how
# often to check; the go/wait decision is always based on real process state.
QUIET_HOURS = range(4, 12)
CHECK_MINUTES_QUIET = 10
CHECK_MINUTES_BUSY = 30

# If DMR is only recording (not rendering) we still accept the test once we
# have waited this long without ever seeing DMR fully idle.
ACCEPT_RECORDING_AFTER_HOURS = 2.0
# ...and only if the machine is otherwise calm.
MAX_BACKGROUND_CPU_PERCENT = 15.0

RECORDER_NAMES = {"streamlink", "streamlink.exe", "streamgears", "streamgears.exe"}
RENDER_SCRIPT_MARKERS = ("emoji_render_mt.py", "render_only.py", "merge_only.py")


def find_dmr_main() -> list[psutil.Process]:
    """Return the running DMR main.py processes (excluding our perf worktree)."""
    found = []
    for proc in psutil.process_iter(["name", "cmdline"]):
        try:
            cmdline = " ".join(proc.info.get("cmdline") or []).replace("/", "\\").lower()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if "main.py" in cmdline and "danmakurender-perf" not in cmdline:
            found.append(proc)
    return found


def classify(proc: psutil.Process) -> str | None:
    """Classify one DMR child process as 'recording', 'rendering' or None.

    Recording ffmpeg reads from the network (http/rtmp input); any other
    ffmpeg started by DMR is a render, transcode or merge job.
    """
    try:
        name = (proc.name() or "").lower()
        cmdline = " ".join(proc.cmdline()).lower()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None
    if name in RECORDER_NAMES:
        return "recording"
    if any(marker in cmdline for marker in RENDER_SCRIPT_MARKERS):
        return "rendering"
    if name in {"ffmpeg", "ffmpeg.exe"}:
        if " -i http" in cmdline or " -i rtmp" in cmdline or "://" in cmdline:
            return "recording"
        return "rendering"
    return None


def dmr_activity() -> dict[str, int]:
    """Count DMR child processes by kind."""
    counts = {"dmr_running": 0, "recording": 0, "rendering": 0}
    for main in find_dmr_main():
        counts["dmr_running"] += 1
        try:
            children = main.children(recursive=True)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        for child in children:
            kind = classify(child)
            if kind:
                counts[kind] += 1
    return counts


def waited_hours() -> float:
    """Hours since we first saw 'wait' for the current test, 0 if not waiting."""
    if not WAIT_SINCE_FILE.exists():
        return 0.0
    started = float(WAIT_SINCE_FILE.read_text().strip() or time.time())
    return (time.time() - started) / 3600.0


def remember_wait_start() -> None:
    if not WAIT_SINCE_FILE.exists():
        WAIT_SINCE_FILE.parent.mkdir(parents=True, exist_ok=True)
        WAIT_SINCE_FILE.write_text(str(time.time()))


def decide() -> dict:
    activity = dmr_activity()
    now = dt.datetime.now()
    in_quiet_hours = now.hour in QUIET_HOURS
    result = {
        "time": now.isoformat(timespec="seconds"),
        **activity,
        "in_quiet_hours": in_quiet_hours,
        "next_check_minutes": CHECK_MINUTES_QUIET if in_quiet_hours else CHECK_MINUTES_BUSY,
        "waited_hours": round(waited_hours(), 2),
    }
    if activity["rendering"]:
        result.update(go=False, reason="DMR 正在渲染/合并/转码")
    elif not activity["recording"]:
        result.update(go=True, reason="DMR 空闲（未录制、未渲染）")
    else:
        background = psutil.cpu_percent(interval=10)
        result["background_cpu_percent"] = background
        if result["waited_hours"] < ACCEPT_RECORDING_AFTER_HOURS:
            result.update(go=False, reason="DMR 正在录制，继续等待完全空闲")
        elif background > MAX_BACKGROUND_CPU_PERCENT:
            result.update(go=False, reason="DMR 正在录制且后台 CPU 偏高")
        else:
            result.update(go=True, reason="只有录制、后台平稳，已等待足够久")
    if not result["go"]:
        remember_wait_start()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reset", action="store_true", help="forget the waiting start time")
    args = parser.parse_args()
    if args.reset:
        WAIT_SINCE_FILE.unlink(missing_ok=True)
        return 0
    result = decide()
    line = json.dumps(result, ensure_ascii=False)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as log:
        log.write(line + "\n")
    print(line)
    return 0 if result["go"] else 10


if __name__ == "__main__":
    sys.exit(main())
