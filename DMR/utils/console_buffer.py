"""
控制台输出镜像：接管 sys.stdout/sys.stderr，原样转发到真实终端的同时，
把每一行复制进内存环形缓冲区，供 WebService 的日志页面读取。
必须在 logging 的 StreamHandler(sys.stdout) 创建之前调用 install_console_tee()，
否则 logging 输出会绕过镜像直接写到原始 stdout。
"""
import sys
import re
import threading
from collections import deque

_ANSI_RE = re.compile(r'\x1b\[[0-9;]*m')
# 报错行匹配（日志级别标记），用于导航“日志”红点的新消息计数
_ERR_RE = re.compile(r'\b(?:ERROR|CRITICAL)\b')

_console_lines = deque(maxlen=2000)
_error_count = [0]   # 单调递增的报错行计数（用列表避免 global）
_lock = threading.Lock()
_installed = False


def _note_line(clean):
    """记录一行（已去 ANSI），命中报错级别则累加计数。调用方需持有 _lock。"""
    _console_lines.append(clean)
    if _ERR_RE.search(clean):
        _error_count[0] += 1


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


def install_console_tee():
    """替换 sys.stdout/sys.stderr 为镜像流，重复调用无效果"""
    global _installed
    if _installed:
        return
    _installed = True
    if sys.stdout is not None:
        sys.stdout = _ConsoleTee(sys.stdout)
    if sys.stderr is not None:
        sys.stderr = _ConsoleTee(sys.stderr)


def get_console_lines(n=500):
    """返回最近 n 行控制台输出"""
    with _lock:
        lines = list(_console_lines)
    return lines[-n:]


def get_error_count():
    """自程序启动以来累计的报错（ERROR/CRITICAL）行数，单调递增。"""
    return _error_count[0]
