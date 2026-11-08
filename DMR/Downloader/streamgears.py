import glob
import json
import logging
import os
import subprocess
import sys
import threading
import time
from os.path import join

from DMR.LiveAPI import Onair
from DMR.utils import uuid


EVENT_PREFIX = "@@DMR_STREAMGEARS@@"


class StreamgearsDownloader:
    default_header = {
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": (
            "Mozilla/5.0 (Linux; Android 5.0; SM-G900P Build/LRX21T) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/75.0.3770.100 Mobile Safari/537.36"
        ),
    }

    def __init__(
        self,
        stream_url: str,
        output_dir: str,
        segment: int,
        url: str,
        taskname: str,
        debug=False,
        header: dict = None,
        segment_start_callback=None,
        segment_end_callback=None,
        **kwargs,
    ):
        self.stream_url = stream_url
        self.header = header if header else self.default_header
        self.output_dir = output_dir
        self.segment = segment
        self.debug = debug
        self.taskname = taskname
        self.url = url
        self.segment_start_callback = segment_start_callback
        self.segment_end_callback = segment_end_callback
        self.kwargs = kwargs

        self.logger = logging.getLogger(__name__)
        self.stoped = False
        self.streamgears_proc = None
        self._event_lock = threading.RLock()
        self._started_files = set()
        self._ended_files = set()
        self._pending_completed = None

    @staticmethod
    def _file_key(path: str) -> str:
        value = os.path.abspath(path)
        if value.lower().endswith(".part"):
            value = value[:-5]
        return os.path.normcase(value)

    def _report_start(self, path: str, started_at: float):
        key = self._file_key(path)
        with self._event_lock:
            if key in self._started_files:
                return
            self._started_files.add(key)
        self.segment_start_callback(path, float(started_at))

    def _report_end(self, path: str, completed_at: float, continuous=False):
        key = self._file_key(path)
        with self._event_lock:
            if key in self._ended_files:
                return
            self._ended_files.add(key)
        callback_path = path
        if not os.path.exists(callback_path) and not callback_path.lower().endswith(".part"):
            part_path = callback_path + ".part"
            if os.path.exists(part_path):
                callback_path = part_path
        self.segment_end_callback(
            callback_path,
            float(completed_at),
            continuous=continuous,
        )

    def _handle_file_started(self, path: str, start_time: float):
        start_time = float(start_time)
        if self._pending_completed:
            old_path, _ = self._pending_completed
            self._pending_completed = None
            self._report_end(old_path, start_time, continuous=True)
        self._report_start(path, start_time)

    def _handle_wrapper_event(self, payload: dict):
        event = payload.get("event")
        if event == "file_started":
            # 新文件的真实创建时间同时作为旧段结束和新段开始，连续段没有缝隙。
            self._handle_file_started(payload["path"], payload["started_at"])
        elif event == "file_completed":
            path = payload["path"]
            observed_path = payload.get("observed_path") or path
            if self._file_key(path) not in self._started_files:
                try:
                    started_at = os.path.getctime(observed_path)
                except OSError:
                    started_at = payload["completed_at"]
                self._handle_file_started(path, started_at)
            # stream-gears 0.2.4 的 Python 回调发生在旧 BufWriter 释放前。
            # wrapper 必须快速返回；父进程在这里稍等文件稳定，再做 FFprobe/重命名。
            last_size = None
            stable_count = 0
            for _ in range(20):
                try:
                    size = os.path.getsize(
                        path if os.path.exists(path) else observed_path
                    )
                except OSError:
                    size = None
                if size is not None and size == last_size:
                    stable_count += 1
                    if stable_count >= 2:
                        break
                else:
                    stable_count = 0
                last_size = size
                time.sleep(0.05)
            # 是否还有下一段要等下一个 file_started 才能确定。若有，使用新文件的
            # 创建时间作为公共边界；若没有，download_finished 会按完成时间收尾。
            self._pending_completed = (path, float(payload["completed_at"]))
        elif event == "download_finished":
            self._finish_pending_completed()
        elif event == "download_error":
            self.logger.error(
                "%s StreamGears 下载错误: %s",
                self.taskname,
                payload.get("error", ""),
            )
        elif self.debug:
            self.logger.debug("%s StreamGears事件: %s", self.taskname, payload)

    def _finish_pending_completed(self):
        if not self._pending_completed:
            return
        path, completed_at = self._pending_completed
        self._pending_completed = None
        self._report_end(path, completed_at, continuous=False)

    def _recover_remaining_files(self, completed_at: float):
        """仅在子进程结束时兜底处理未收到完成事件的当前文件。"""
        files = sorted(glob.glob(join(self.output_dir, f"*{self.uuid}*")))
        for observed_path in files:
            canonical = observed_path[:-5] if observed_path.lower().endswith(".part") else observed_path
            path = canonical if os.path.exists(canonical) else observed_path
            key = self._file_key(path)
            if key not in self._started_files:
                try:
                    started_at = os.path.getctime(observed_path)
                except OSError:
                    started_at = completed_at
                self._report_start(path, started_at)
            if key not in self._ended_files:
                self._report_end(path, completed_at)

    def start_helper(self):
        raw_name = join(
            self.output_dir,
            f"[正在录制]{self.taskname}-%Y%m%d%H%M%S-{self.uuid}",
        )
        streamgears_args = [
            sys.executable,
            "DMR/Downloader/streamgears_wrapper.py",
            "-i",
            self.stream_url,
            "-o",
            raw_name,
            "-s",
            self.segment,
            "--header",
            json.dumps(self.header),
            "--marker",
            self.uuid,
        ]
        streamgears_args = [str(value) for value in streamgears_args]
        self.logger.debug("Stream-gears downloader args: %s", streamgears_args)

        wrapper_env = os.environ.copy()
        wrapper_env["PYTHONIOENCODING"] = "utf-8"
        self.streamgears_proc = subprocess.Popen(
            streamgears_args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            # Wrapper protocol is ASCII-only JSON.  Never silently drop bytes:
            # doing so can turn a valid Unicode path into a different path.
            errors="replace",
            bufsize=1,
            env=wrapper_env,
        )

        assert self.streamgears_proc.stdout is not None
        for raw_line in self.streamgears_proc.stdout:
            line = raw_line.rstrip()
            if line.startswith(EVENT_PREFIX):
                try:
                    payload = json.loads(line[len(EVENT_PREFIX):])
                    self._handle_wrapper_event(payload)
                except Exception:
                    self.logger.exception("解析 StreamGears 分段事件失败: %s", line)
            elif line and self.debug:
                self.logger.debug("%s StreamGears: %s", self.taskname, line)

        return_code = self.streamgears_proc.wait()
        finished_at = time.time()
        self._finish_pending_completed()
        self._recover_remaining_files(finished_at)
        if not self.stoped and return_code and Onair(self.url):
            raise RuntimeError(
                f"{self.taskname} Stream-gears 异常退出，返回码 {return_code}."
            )

    def start(self):
        self.stoped = False
        self.uuid = uuid(16)
        self._started_files.clear()
        self._ended_files.clear()
        self._pending_completed = None
        return self.start_helper()

    def stop(self):
        if self.stoped:
            return
        self.stoped = True
        try:
            if self.streamgears_proc and self.streamgears_proc.poll() is None:
                self.streamgears_proc.kill()
                self.streamgears_proc.wait(timeout=3)
        except Exception:
            self.logger.exception("%s StreamGears 子进程停止失败", self.taskname)

        self._finish_pending_completed()
        self._recover_remaining_files(time.time())
        self.logger.debug("Stream-gears downloader stopped.")
