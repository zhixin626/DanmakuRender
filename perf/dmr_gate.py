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
import urllib.request
import yaml

import psutil

PERF_DIR = pathlib.Path(__file__).resolve().parent
WAIT_SINCE_FILE = PERF_DIR / "tmp" / "gate_wait_since.txt"
LOG_FILE = PERF_DIR / "tmp" / "gate.log"

# Hours (local time) in which the PC is usually idle. Only used to pick how
# often to check; the go/wait decision is always based on real process state.
QUIET_HOURS = range(4, 12)
CHECK_MINUTES_QUIET = 10
CHECK_MINUTES_BUSY = 30

# Recording alone is light, so testing during recording is fine. The real risk
# is a 2-hour segment ending mid-test: DMR then starts rendering it. So we only
# start when the current segment still has enough time left for one test run.
SEGMENT_SECONDS = 7200          # download_args.live.segment in configs/global.yml
DEFAULT_NEED_MINUTES = 40       # time one test run needs; override with --need-minutes
MAX_BACKGROUND_CPU_PERCENT = 25.0

RECORDER_NAMES = {"streamlink", "streamlink.exe", "streamgears", "streamgears.exe"}
RENDER_SCRIPT_MARKERS = ("emoji_render_mt.py", "render_only.py", "merge_only.py")


def find_dmr_main() -> list[psutil.Process]:
    """Find DMR in any checkout, including the worktree and Python launchers."""
    found = []
    for proc in psutil.process_iter(["name", "cmdline"]):
        try:
            command = proc.info.get('cmdline') or []
            is_python = (proc.info.get('name') or '').lower() in ('python','python.exe','pythonw.exe')
            is_main = any(pathlib.PureWindowsPath(arg).name.lower() == 'main.py' for arg in command)
            root = pathlib.Path(proc.cwd())
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if is_python and is_main and (root / 'DMR').is_dir():
            found.append(proc)
    # The venv launcher and its real interpreter have the same command. Inspect
    # their tree once, otherwise recording counts and boundaries are duplicated.
    ids = {proc.pid for proc in found}
    roots = []
    for proc in found:
        try:
            if proc.ppid() not in ids:
                roots.append(proc)
        except (psutil.NoSuchProcess,psutil.AccessDenied):
            continue
    return roots


def web_activity(main):
    """Read in-process GPU work from this instance's configured local WebUI.

    GPU rendering runs in a main.py thread, with no renderer subprocess. A
    missing/unreadable API is unknown, never evidence that the GPU is idle.
    """
    root = pathlib.Path(main.cwd())
    command = main.cmdline()
    config_path = root / 'configs/global.yml'
    if '--config' in command:
        config_path = root / command[command.index('--config')+1]
    base = yaml.safe_load((root/'DMR/Config/default.yml').read_text(encoding='utf-8'))
    current = yaml.safe_load(config_path.read_text(encoding='utf-8')) or {}
    web = {**base['webservice_kernel_args'],**current.get('webservice_kernel_args',{})}
    # Always query loopback, even when the UI listens on all interfaces.
    url = f"http://127.0.0.1:{int(web['port'])}/api/tasks"
    with urllib.request.urlopen(url,timeout=2) as response:
        return json.load(response)


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


def dmr_activity() -> dict:
    """Count DMR child processes by kind and remember when recorders started."""
    counts = {"dmr_running": 0, "recording": 0, "rendering": 0, "recorder_started": [], 'unknown_instances':[]}
    for main in find_dmr_main():
        counts["dmr_running"] += 1
        try:
            children = main.children(recursive=True)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        child_rendering = 0
        child_recording = 0
        for child in children:
            kind = classify(child)
            if not kind:
                continue
            child_rendering += kind == 'rendering'
            child_recording += kind == 'recording'
            if kind == "recording":
                try:
                    counts["recorder_started"].append(child.create_time())
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        try:
            data = web_activity(main)
            api_rendering = len(data['render_queue']['active']) + len(data['merge_queue']['active'])
            api_recording = sum(item['status'] == 1 for item in data['recording_tasks'])
        except (OSError,ValueError,KeyError,IndexError,psutil.Error,yaml.YAMLError):
            counts['unknown_instances'].append(main.pid)
            api_rendering = api_recording = 0
        counts['rendering'] += max(child_rendering, api_rendering)
        counts['recording'] += max(child_recording, api_recording)
    return counts


def minutes_to_next_segment(recorder_started: list[float]) -> float:
    """Estimate minutes until the earliest recording reaches a segment boundary.

    Assumes a segment boundary every SEGMENT_SECONDS after the recorder process
    started. If DMR restarts the recorder for each segment this is exact; if it
    keeps one process, the modulo still gives the right boundary.
    """
    now = time.time()
    remaining = [SEGMENT_SECONDS - (now - start) % SEGMENT_SECONDS for start in recorder_started]
    return min(remaining) / 60.0


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


def decide(need_minutes: float) -> dict:
    activity = dmr_activity()
    recorder_started = activity.pop("recorder_started")
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
    elif activity['unknown_instances']:
        result.update(go=False, reason='DMR 状态接口不可读取，无法排除进程内 GPU 渲染')
    elif not activity["recording"]:
        result.update(go=True, reason="DMR 空闲（未录制、未渲染）")
    else:
        background = psutil.cpu_percent(interval=10)
        left = minutes_to_next_segment(recorder_started) if recorder_started else None
        result["background_cpu_percent"] = background
        result["minutes_to_next_segment"] = None if left is None else round(left, 1)
        if left is not None and left < need_minutes:
            result.update(go=False, reason=f"录制分段约 {left:.0f} 分钟后结束，不够跑完测试，等这次渲染结束")
        elif background > MAX_BACKGROUND_CPU_PERCENT:
            result.update(go=False, reason="DMR 正在录制且后台 CPU 偏高")
        else:
            result.update(go=True, reason="只在录制，离分段结束还有足够时间")
    if not result["go"]:
        remember_wait_start()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reset", action="store_true", help="forget the waiting start time")
    parser.add_argument("--need-minutes", type=float, default=DEFAULT_NEED_MINUTES,
                        help="how long the planned test run takes (minutes)")
    args = parser.parse_args()
    if args.reset:
        WAIT_SINCE_FILE.unlink(missing_ok=True)
        return 0
    result = decide(args.need_minutes)
    line = json.dumps(result, ensure_ascii=False)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as log:
        log.write(line + "\n")
    print(line)
    return 0 if result["go"] else 10


if __name__ == "__main__":
    sys.exit(main())
