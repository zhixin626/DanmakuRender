"""Exercise the BAT/main.py task-dispatch entry without starting DMR."""

from types import SimpleNamespace
from unittest.mock import Mock

from DMR.Task.liveevents import LiveEvents
from DMR.Render import Render
from DMR.Render import gpu_render as gpu
from DMR.utils import VideoInfo
import queue


def test_live_recording_dispatch_preserves_gpu_backend():
    handler = LiveEvents.__new__(LiveEvents)
    handler.name = "月亮3"
    handler.logger = Mock()
    options = {"engine": "gpu_render", "format": "mp4"}
    handler.config = {
        "common_event_args": {"auto_render": True},
        "render_args": {"overlay": options},
    }
    slot = {"wait": [], "status": "ready"}
    handler.state_dict = {"recording": [{"dm_video": slot}]}
    handler._dispatched = {}
    video = SimpleNamespace(path="recording.flv", group_id="recording")
    (message,) = handler._check_for_render(video)
    assert message.target == "render"
    assert message.data["mode"] == "gpu_render"
    assert message.data["args"] is options
    assert slot["status"] == "rendering" and slot["wait"] == [message.request_id]
    handler.logger.warning.assert_not_called()


def test_gpu_result_runs_through_render_end_and_live_state(monkeypatch, tmp_path):
    """The exact dict/group_id failure must traverse the real plugin callback."""
    source = VideoInfo(
        path=str(tmp_path / "source.mp4"),
        file_id="source",
        group_id="live",
        segment_id=7,
        taskname="moon",
        resolution=(1920, 1080),
        duration=1,
    )
    destination = tmp_path / "output.mp4"

    def rendered(video, output, **_):
        destination.write_bytes(b"encoded")
        return {"width": 1920, "height": 1080, "frames": 60, "frame_rate": 60}

    monkeypatch.setattr(gpu, "render_one", rendered)
    send = queue.Queue()
    plugin = Render(
        (send, queue.Queue()), failed_tasks_file=str(tmp_path / "failed.json")
    )
    plugin.stoped = False
    task = dict(
        uuid="render-id",
        source="replay/moon",
        request_id="request-id",
        mode="gpu_render",
        args={"engine": "gpu_render", "retry": 0},
        video=source,
        output=str(destination),
        config={"mode": "gpu_render"},
    )
    plugin.render_tasks[task["uuid"]] = task
    plugin._render_subprocess(task)
    message = send.get_nowait()
    assert message.event == "end"
    result = message.data["output"]
    assert isinstance(result, VideoInfo)
    assert (
        result.group_id == "live"
        and result.segment_id == 7
        and result.src_video_id == "source"
    )
    assert result.path == str(destination) and result.dtype == "dm_video"
    assert source.path != result.path and source.dtype != "dm_video"
    handler = LiveEvents.__new__(LiveEvents)
    handler.name = "moon"
    handler.logger = Mock()
    handler._dispatched = {"request-id": object()}
    slot = {"wait": ["request-id"], "status": "rendering", "file": None}
    handler.state_dict = {"live": [{"dm_video": slot}]}
    handler.reconcile = Mock(return_value=[])
    assert handler.onRenderEnd(message) == []
    assert slot["status"] == "ready" and slot["file"] is result and not slot["wait"]
    handler.reconcile.assert_called_once_with("live", clean_trigger="onRenderEnd")
    plugin.render_executors.shutdown()
