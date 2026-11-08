import argparse
import glob
import json
import os
import threading
import time

import stream_gears


EVENT_PREFIX = "@@DMR_STREAMGEARS@@"
_print_lock = threading.Lock()


def emit(event: str, **data) -> None:
    """Write one machine-readable event using ASCII-only JSON.

    On Windows a Python child connected to a pipe commonly uses the active
    ANSI code page for stdout.  Keeping the protocol ASCII-only makes it
    independent of that code page; JSON decoding restores the original
    Unicode path in the parent process.
    """
    payload = {"event": event, **data}
    with _print_lock:
        print(
            EVENT_PREFIX + json.dumps(payload, ensure_ascii=True, separators=(",", ":")),
            flush=True,
        )


def canonical_path(path: str) -> str:
    value = os.path.abspath(path)
    return value[:-5] if value.lower().endswith(".part") else value


def make_segment(seconds: int):
    segment_class = getattr(stream_gears, "Segment", None)
    if segment_class is not None and hasattr(segment_class, "by_time"):
        return segment_class.by_time(seconds)

    # stream-gears 0.2.x 仍接受带 time 属性的简单对象。
    class Segment:
        pass

    segment = Segment()
    segment.time = seconds
    return segment


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("-i", "--input", type=str, required=True)
    parser.add_argument("-o", "--output", type=str, default="")
    parser.add_argument("-s", "--segment", type=int, default=3600)
    parser.add_argument("--header", type=str, default=r"{}")
    parser.add_argument("--marker", type=str, required=True)
    args = parser.parse_args()

    try:
        header = json.loads(args.header)
    except Exception:
        header = {}

    stop_watcher = threading.Event()
    started = set()
    output_dir = os.path.dirname(os.path.abspath(args.output)) or "."

    def watch_created_files():
        pattern = os.path.join(output_dir, f"*{args.marker}*")
        while not stop_watcher.is_set():
            for observed_path in glob.glob(pattern):
                if not os.path.isfile(observed_path):
                    continue
                path = canonical_path(observed_path)
                if path in started:
                    continue
                started.add(path)
                try:
                    started_at = os.path.getctime(observed_path)
                except OSError:
                    started_at = time.time()
                emit(
                    "file_started",
                    path=path,
                    observed_path=os.path.abspath(observed_path),
                    started_at=started_at,
                )
            stop_watcher.wait(0.05)

    watcher = threading.Thread(
        target=watch_created_files,
        daemon=True,
        name="streamgears-file-watcher",
    )
    watcher.start()

    def file_completed(path: str):
        observed_path = os.path.abspath(path)
        completed_path = canonical_path(path)
        if completed_path not in started:
            started.add(completed_path)
            try:
                started_at = os.path.getctime(path)
            except OSError:
                started_at = time.time()
            emit("file_started", path=completed_path, started_at=started_at)
        emit(
            "file_completed",
            path=completed_path,
            observed_path=observed_path,
            completed_at=time.time(),
        )

    emit("download_started", started_at=time.time())
    try:
        stream_gears.download_with_callback(
            args.input,
            header,
            args.output,
            make_segment(args.segment),
            file_completed,
            None,
        )
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        emit("download_error", error=repr(exc), occurred_at=time.time())
        raise
    finally:
        stop_watcher.set()
        watcher.join(timeout=0.2)
        emit("download_finished", finished_at=time.time())


if __name__ == "__main__":
    main()
