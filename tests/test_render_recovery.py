"""Stopped renders remain independently retryable without live-group storage."""

import json
import queue
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from DMR.Task.liveevents import LiveEvents
from DMR.Render import Render
from DMR.Render import gpu_render as gpu
from DMR.Render.baserender import RenderStopped
from DMR.Render.sprites import resolve_font
from DMR.WebService.webapi import WebApi
from DMR.utils import VideoInfo, FFprobe, PipeMessage


@pytest.mark.parametrize("restart", [False, True])
def test_stop_finishes_group_and_failed_list_can_retry(monkeypatch, tmp_path, restart):
    options = yaml.safe_load(Path("DMR/Config/default.yml").read_text(encoding="utf8"))[
        "render_args"
    ]["overlay"]
    options.update(engine="gpu_render", output_dir=str(tmp_path / "rendered"))
    config = {
        "download_args": {"output_dir": str(tmp_path), "dltype": "live"},
        "render_args": {"overlay": options},
        "common_event_args": {
            "auto_render": True,
            "auto_upload": False,
            "auto_merge": True,
            "auto_clean": False,
        },
        "merge_args": {"merge_type": ["dm_video"]},
    }
    handler = LiveEvents("test", config)
    monkeypatch.setattr(handler, "_collect_session_data", lambda _: None)
    source = tmp_path / "source.flv"
    source.write_bytes(b"source")
    events = tmp_path / "source.jsonl"
    events.write_text(
        '{"type":"meta","schema":1,"platform":"bilibili","segment_start_time":0}\n',
        encoding="utf8",
    )
    video = VideoInfo(
        path=str(source),
        dm_file_id=str(events),
        duration=1,
        resolution=(1920, 1080),
        group_id="session",
        segment_id=1,
        file_id="original",
        ctime=datetime.now(),
    )
    monkeypatch.setattr(FFprobe, "get_timeline_duration", lambda _: 1)
    calls = []

    def worker(video, output, stop_event, **kwargs):
        calls.append(output)
        if len(calls) == 1:
            stop_event.set()
            raise RenderStopped("user stop")
        Path(output).write_bytes(b"completed independently")
        return {"width": 1920, "height": 1080, "frames": 50, "frame_rate": 50}

    monkeypatch.setattr(gpu, "render_one", worker)
    send = queue.Queue()
    failed_file = tmp_path / "failed.json"
    plugin = Render((send, queue.Queue()), failed_tasks_file=str(failed_file))
    plugin.stoped = False
    try:
        handler.session_data["session"] = {"is_live_end": False}
        messages = handler.onLiveSegment(
            PipeMessage("downloader", "test", "livesegment", data=video)
        )
        (dispatch,) = [
            m for m in messages if m.target == "render" and m.event == "newtask"
        ]
        handler.onLiveEnd(PipeMessage("downloader", "test", "liveend", data="session"))
        plugin.add_task(dispatch)
        stopped = send.get(timeout=5)
        assert stopped.event == "stopped"
        handler.onRenderStop(stopped)
        assert handler._group_is_done("session") and handler.is_idle_for_restart()
        assert not handler.state_dict and not handler.held_tasks
        assert not (tmp_path / ".dmr").exists()
        (task_id,) = plugin.failed_tasks
        assert task_id in json.loads(failed_file.read_text(encoding="utf8"))
        if restart:
            plugin.stop()
            plugin.render_executors.shutdown()
            plugin = Render((send, queue.Queue()), failed_tasks_file=str(failed_file))
            plugin.stoped = False
            assert task_id in plugin.failed_tasks and isinstance(
                plugin.failed_tasks[task_id]["video"].ctime, datetime
            )
        engine = SimpleNamespace(
            plugin_dict={"render": {"class": plugin}}, task_dict={}, notifications=[]
        )
        api = WebApi((queue.Queue(), queue.Queue()), engine=engine, force_login=False)
        app = api.create_app()
        client = app.test_client()
        status = client.get("/api/tasks").get_json()
        assert status["notifications"]["failed"] == 1
        rows = status["held_tasks"]
        (row,) = [row for row in rows if row["hold_id"] == "render:" + task_id]
        assert row["independent_render"] and row["task"] == "test"
        response = client.post(
            "/api/retry", json={"task": "test", "hold_id": "render:" + task_id}
        )
        assert response.status_code == 200
        done = send.get(timeout=5)
        assert done.event == "end" and done.request_id != dispatch.request_id
        assert handler.onRenderEnd(done) == []  # no group recreation, merge or upload
        assert Path(done.data["output"].path).is_file()
        assert not plugin.failed_tasks and not json.loads(
            failed_file.read_text(encoding="utf8")
        )
    finally:
        plugin.render_executors.shutdown()


def test_failed_retry_is_not_lost_and_can_be_ignored(tmp_path, monkeypatch):
    plugin = Render(
        (queue.Queue(), queue.Queue()), failed_tasks_file=str(tmp_path / "failed.json")
    )
    plugin.stoped = False
    video = VideoInfo(path="missing.flv", group_id="released")
    task = {
        "uuid": "id",
        "source": "replay/test",
        "request_id": "old",
        "video": video,
        "mode": "gpu_render",
        "args": {},
        "config": {"mode": "gpu_render"},
        "output": "out.mp4",
    }
    plugin._gather(task, "stopped", RenderStopped("stop"))
    monkeypatch.setattr(
        plugin,
        "_render_subprocess",
        lambda task: plugin._gather(task, "error", RuntimeError("bad source")),
    )
    try:
        assert plugin.retry_task("id")
        plugin.render_executors.shutdown()
        assert plugin.failed_tasks["id"]["status"] == "error"
        assert json.loads((tmp_path / "failed.json").read_text(encoding="utf8"))
        engine = SimpleNamespace(
            plugin_dict={"render": {"class": plugin}}, task_dict={}, notifications=[]
        )
        client = (
            WebApi((queue.Queue(), queue.Queue()), engine=engine, force_login=False)
            .create_app()
            .test_client()
        )
        assert (
            client.post(
                "/api/resume",
                json={"task": "test", "hold_id": "render:id", "result": "__ignore__"},
            ).status_code
            == 200
        )
        assert not plugin.failed_tasks
    finally:
        plugin.render_executors.shutdown()


@pytest.mark.parametrize("bold", [False, True])
def test_missing_font_warns_and_falls_back_to_chinese(caplog, bold):
    path = resolve_font("This Font Does Not Exist 734823", bold)
    assert Path(path).is_file()
    assert ("msyhbd" if bold else "msyh") in Path(path).name.lower()
    assert "回退" in caplog.text
