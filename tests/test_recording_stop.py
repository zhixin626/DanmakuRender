"""Stopping a recording must not lose the danmaku file of the last video."""

import logging
import threading
from types import SimpleNamespace

from DMR.Downloader.ffmpeg import FFmpegDownloader
from DMR.Downloader.stream_downloader import StreamDownloadTask


def test_ffmpeg_reports_the_last_file_only_once():
    downloader = FFmpegDownloader.__new__(FFmpegDownloader)
    reported = []
    downloader.segment_end_callback = lambda name, end: reported.append(name)
    downloader._finish_lock = threading.Lock()
    downloader.thisfile = "part000.flv"
    # stop() and the recording thread (FFmpeg killed by Ctrl+C) both finish it.
    downloader._finish_current_segment(1.0)
    downloader._finish_current_segment(2.0)
    assert reported == ["part000.flv"]


def make_task(pending_contexts):
    task = StreamDownloadTask.__new__(StreamDownloadTask)
    calls = []
    task.video = True
    task.danmaku = True
    task.logger = logging.getLogger("test")
    task.downloader = SimpleNamespace(stop=lambda: None)
    task.dmw = SimpleNamespace(stop=lambda discard_unpaired: calls.append(discard_unpaired))
    task._segment_lock = threading.Lock()
    task._segment_contexts = dict(pending_contexts)
    task._video_future = None
    return task, calls


def test_stop_keeps_danmaku_while_a_video_segment_is_unfinished():
    task, calls = make_task({"part000.flv": {"start_time": None}})
    task.stop_once()
    assert calls == [False]


def test_stop_discards_danmaku_only_when_every_video_is_finished():
    task, calls = make_task({})
    task.stop_once()
    assert calls == [True]
