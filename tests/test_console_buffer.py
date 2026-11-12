import io
import threading
from collections import deque

import DMR.utils.console_buffer as console


def _reset(tmp_path):
    console._console_lines = deque(maxlen=20)
    console._subscribers.clear()
    console._next_id[0] = 0
    console._file_index.clear()
    console._log_path = tmp_path / "console.txt"
    console._log_file = console._log_path.open("ab")
    console._file_offset = 0
    console._last_flush = 0
    return console._ConsoleTee(io.StringIO())


def _close():
    if console._log_file is not None:
        console._log_file.flush()
        console._log_file.close()
        console._log_file = None


def test_concurrent_lines_are_monotonic_and_contiguous(tmp_path):
    tee = _reset(tmp_path)
    try:
        threads = [
            threading.Thread(
                target=lambda prefix=prefix: [
                    tee.write(f"{prefix}-{i}\n") for i in range(200)
                ]
            )
            for prefix in range(4)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        console._log_file.flush()
        ids = [
            int(line.split("\t", 1)[0])
            for line in console._log_path.read_text(encoding="utf8").split("\n")
            if line
        ]
        assert ids == list(range(1, 801))
    finally:
        _close()


def test_pagination_crosses_memory_file_boundary(tmp_path):
    tee = _reset(tmp_path)
    try:
        for i in range(1200):
            tee.write(f"line-{i}\n")
        page = console.get_console_history(1100, 50)
        assert [item["id"] for item in page] == list(range(1050, 1100))
    finally:
        _close()


def test_u2028_and_last_id_are_preserved(tmp_path):
    tee = _reset(tmp_path)
    try:
        tee.write("before\u2028after\n")
        tee.write("second\n")
        _, history = console.subscribe(10, 1)
        assert [item["id"] for item in history] == [2]
        assert "\u2028" in console._log_path.read_text(encoding="utf8")
    finally:
        _close()


class _CountingPath(type(console.Path())):
    """测试替身：记录分页时从日志文件实际读出的行数。"""
    lines_read = 0

    def open(self, *args, **kwargs):
        stream = super().open(*args, **kwargs)
        path = self

        class Counting:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                stream.close()

            def seek(self, offset):
                return stream.seek(offset)

            def __iter__(self):
                for line in stream:
                    type(path).lines_read += 1
                    yield line

        return Counting()


def test_paging_large_file_reads_bounded_lines(tmp_path):
    tee = _reset(tmp_path)
    try:
        total, limit = 100_000, 500
        tee.write("".join(f"line-{i}\n" for i in range(1, total + 1)))
        console._log_path = _CountingPath(console._log_path)

        before = total + 1
        seen = []
        while True:
            _CountingPath.lines_read = 0
            page = console.get_console_history(before, limit)
            if not page:
                break
            assert _CountingPath.lines_read <= limit + console.INDEX_INTERVAL + 1
            assert [item["id"] for item in page] == list(range(page[0]["id"], before))
            seen.extend(reversed(page))
            before = page[0]["id"]
        assert [item["id"] for item in reversed(seen)] == list(range(1, total + 1))
        assert seen[-1]["text"] == "line-1"
    finally:
        _close()


def test_history_pages_stay_contiguous_while_writing(tmp_path):
    tee = _reset(tmp_path)
    thread = threading.Thread(
        target=lambda: [tee.write(f"line-{i}\n") for i in range(1, 20_001)]
    )
    thread.start()
    try:
        while thread.is_alive():
            before = console._next_id[0]
            page = console.get_console_history(before, 50)
            ids = [item["id"] for item in page]
            assert ids == list(range(before - len(ids), before))
            assert all(item["text"] == f"line-{item['id']}" for item in page)
    finally:
        thread.join()
        _close()

