"""
控制台输出镜像：接管 sys.stdout/sys.stderr，原样转发到真实终端的同时，
把每一行编上序号复制进内存环形缓冲区，并落盘到 logs/console-启动时间.txt，
供 WebService 日志页实时推送、断线续传和向上翻页读取。
必须在 logging 的 StreamHandler(sys.stdout) 创建之前调用 install_console_tee()，
否则 logging 输出会绕过镜像直接写到原始 stdout。
"""
import sys
import re
import queue
import threading
import time
import atexit
from pathlib import Path
from collections import deque

_ANSI_RE = re.compile(r'\x1b\[[0-9;]*m')
# 报错行匹配（日志级别标记），用于导航“日志”红点的新消息计数
_ERR_RE = re.compile(r'\b(?:ERROR|CRITICAL)\b')

# 内存里保留的最近日志行：仅用于 SSE 连上时「发一段历史打底」，上限保证内存有界。
MEMORY_LINES = 20000
_console_lines = deque(maxlen=MEMORY_LINES)
_error_count = [0]   # 单调递增的报错行计数（用列表避免 global）
_next_id = [0]
_log_path = None
_log_file = None
_file_index = []      # 每 INDEX_INTERVAL 行记录一次 (序号, 文件偏移)，分页时据此 seek
INDEX_INTERVAL = 1000
_file_offset = 0
_last_flush = 0.0
_lock = threading.Lock()
_installed = False
_subscribers = set()  # SSE 订阅者的队列集合：每来一行就推给它们（实时流式，不再靠轮询）


def _note_line(clean):
    """记录一行（已去 ANSI），命中报错级别则累加计数，并推给所有 SSE 订阅者。调用方需持有 _lock。"""
    _next_id[0] += 1
    item = {"id": _next_id[0], "text": clean}
    _console_lines.append(item)
    _persist_item(item)
    if _ERR_RE.search(clean):
        _error_count[0] += 1
    # 推给订阅者；某个队列满了（客户端太慢/已死）就丢这行，绝不阻塞日志写入
    for q in list(_subscribers):
        try:
            q.put_nowait(item)
        except queue.Full:
            pass
    return item


def _persist_item(item):
    global _file_offset, _last_flush
    if _log_file is None:
        return
    try:
        encoded = f"{item['id']}\t{item['text'].replace(chr(10), ' ')}\n".encode('utf-8')
        offset = _file_offset
        _log_file.write(encoded)
        _file_offset += len(encoded)
        if item["id"] % INDEX_INTERVAL == 0:
            _file_index.append((item["id"], offset))
        now = time.monotonic()
        if now - _last_flush >= 1.0:
            _log_file.flush()
            _last_flush = now
    except OSError:
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
                lines = (self._partial + text).split('\n')
                self._partial = lines.pop()
                for line in lines:
                    _note_line(_ANSI_RE.sub('', line).rstrip('\r'))
                # 防止只有 \r 刷新（如进度条）导致缓存无限增长
                if len(self._partial) > 8192:
                    _note_line(_ANSI_RE.sub('', self._partial))
                    self._partial = ''
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


def install_console_tee(log_dir):
    """替换 sys.stdout/sys.stderr 为镜像流并在 log_dir 下落盘，重复调用无效果"""
    global _installed, _log_path, _log_file, _file_offset
    if _installed:
        return
    _installed = True
    _log_path = Path(log_dir) / f"console-{time.strftime('%Y%m%d-%H%M%S')}.txt"
    _log_file = _log_path.open("ab")
    _file_offset = _log_path.stat().st_size
    atexit.register(_close_log_file)
    if sys.stdout is not None:
        sys.stdout = _ConsoleTee(sys.stdout)
    if sys.stderr is not None:
        sys.stderr = _ConsoleTee(sys.stderr)


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
    """返回序号小于 before_id 的最近 limit 行（before_id<=0 表示最新），正序。
    内存里不够的部分按偏移索引从落盘文件读取，读取量不超过 limit+索引间隔。"""
    with _lock:
        records = [item for item in _console_lines if before_id <= 0 or item["id"] < before_id][-limit:]
        target = records[0]["id"] if records else before_id
        need = limit - len(records)
        if before_id <= 0 or need <= 0 or target <= 1 or _log_file is None:
            return records
        # 写入在锁内完成，flush 也必须在锁内，否则可能读到半行
        _log_file.flush()
        path = _log_path
        start_id = max(1, target - need)
        offset = 0
        for item_id, item_offset in _file_index:
            if item_id > start_id:
                break
            offset = item_offset
    older = []
    try:
        with path.open("rb") as stream:
            stream.seek(offset)
            for line in stream:
                raw_id, _, raw_text = line.rstrip(b"\n").partition(b"\t")
                try:
                    item_id = int(raw_id)
                except ValueError:
                    continue
                if item_id >= target:
                    break
                if item_id >= start_id:
                    older.append({"id": item_id, "text": raw_text.decode("utf-8", errors="replace")})
    except OSError:
        pass
    return older + records


def _close_log_file():
    global _log_file
    with _lock:
        if _log_file is not None:
            try:
                _log_file.close()
            except OSError:
                pass
            _log_file = None


def unsubscribe(q):
    """取消订阅（SSE 连接断开时调用），把队列从订阅集合里移除。"""
    with _lock:
        _subscribers.discard(q)
