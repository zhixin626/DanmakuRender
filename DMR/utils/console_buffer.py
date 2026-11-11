"""
控制台输出镜像：接管 sys.stdout/sys.stderr，原样转发到真实终端的同时，
把每一行复制进内存环形缓冲区，供 WebService 的日志页面读取。
必须在 logging 的 StreamHandler(sys.stdout) 创建之前调用 install_console_tee()，
否则 logging 输出会绕过镜像直接写到原始 stdout。
"""
import sys
import re
import queue
import threading
import os
from pathlib import Path
import time
from collections import deque

_ANSI_RE = re.compile(r'\x1b\[[0-9;]*m')
# 报错行匹配（日志级别标记），用于导航“日志”红点的新消息计数
_ERR_RE = re.compile(r'\b(?:ERROR|CRITICAL)\b')

# 内存里保留的最近日志行：仅用于 SSE 连上时「发一段历史打底」，上限保证内存有界。
_console_lines = deque(maxlen=20000)
_error_count = [0]   # 单调递增的报错行计数（用列表避免 global）
_next_id = [0]
_log_path = None
_lock = threading.Lock()
_installed = False
_subscribers = set()  # SSE 订阅者的队列集合：每来一行就推给它们（实时流式，不再靠轮询）


def _note_line(clean):
    """记录一行（已去 ANSI），命中报错级别则累加计数，并推给所有 SSE 订阅者。调用方需持有 _lock。"""
    _next_id[0] += 1
    item = {"id": _next_id[0], "text": clean}
    _console_lines.append(item)
    if _log_path is not None:
        try:
            with _log_path.open("a", encoding="utf-8") as stream:
                stream.write(f"{item['id']}\t{clean.replace(chr(10), ' ')}\n")
        except OSError:
            pass
    if _ERR_RE.search(clean):
        _error_count[0] += 1
    # 推给订阅者；某个队列满了（客户端太慢/已死）就丢这行，绝不阻塞日志写入
    for q in list(_subscribers):
        try:
            q.put_nowait(clean)
        except queue.Full:
            pass


class _ConsoleTee:
    def __init__(self, stream):
        self._stream = stream
        self._partial = ''

    def write(self, text):
        try:
            self._stream.write(text)
        except Exception:
            pass
        try:
            with _lock:
                self._partial += text
                # 防止只有 \r 刷新（如进度条）导致缓存无限增长
                if '\n' not in self._partial and len(self._partial) > 8192:
                    _note_line(_ANSI_RE.sub('', self._partial))
                    self._partial = ''
                while '\n' in self._partial:
                    line, self._partial = self._partial.split('\n', 1)
                    _note_line(_ANSI_RE.sub('', line).rstrip('\r'))
        except Exception:
            pass
        return len(text)

    def flush(self):
        try:
            self._stream.flush()
        except Exception:
            pass

    def __getattr__(self, name):
        return getattr(self._stream, name)


def install_console_tee(log_dir=None):
    """替换 sys.stdout/sys.stderr 为镜像流，重复调用无效果"""
    global _installed
    if _installed:
        return
    _installed = True
    global _log_path
    if log_dir:
        path = Path(log_dir)
        path.mkdir(parents=True, exist_ok=True)
        _log_path = path / f"console-{time.strftime('%Y%m%d-%H%M%S')}.txt"
    if sys.stdout is not None:
        sys.stdout = _ConsoleTee(sys.stdout)
    if sys.stderr is not None:
        sys.stderr = _ConsoleTee(sys.stderr)


def get_console_lines(n=500):
    """返回最近 n 行控制台输出"""
    with _lock:
        lines = list(_console_lines)
    return [item["text"] for item in lines[-n:]]


def get_error_count():
    """自程序启动以来累计的报错（ERROR/CRITICAL）行数，单调递增。"""
    return _error_count[0]


def subscribe(history_size=0, last_id=0):
    """原子地取得历史快照并订阅新日志，避免两步操作造成遗漏或重复。"""
    q = queue.Queue(maxsize=10000)
    with _lock:
        history = [item for item in _console_lines if item["id"] > last_id]
        if history_size:
            history = history[-history_size:]
        _subscribers.add(q)
    return q, history


def get_console_history(before_id=0, limit=500):
    """从内存和落盘文件中取得更早历史，按序号倒序筛选后正序返回。"""
    with _lock:
        records = [item for item in _console_lines if before_id <= 0 or item["id"] < before_id]
        path = _log_path
    if path and path.exists() and len(records) < limit:
        try:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                raw_id, _, text = line.partition("\t")
                try:
                    item = {"id": int(raw_id), "text": text}
                except ValueError:
                    continue
                if item["id"] < before_id and all(old["id"] != item["id"] for old in records):
                    records.append(item)
        except OSError:
            pass
    return sorted(records, key=lambda item: item["id"])[-limit:]


def unsubscribe(q):
    """取消订阅（SSE 连接断开时调用），把队列从订阅集合里移除。"""
    with _lock:
        _subscribers.discard(q)
