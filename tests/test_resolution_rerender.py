import queue
from datetime import datetime

from DMR.Task.liveevents import LiveEvents
from DMR.utils import PipeMessage, VideoInfo


def make_handler(tmp_path):
    overlay = {
        "engine": "gpu_render",
        "rescale_to_1080p": True,
        "rescale_orientation": "auto",
        "resolution_ignore_head_seconds": 0,
        "resolution_min_span_seconds": 0,
        "resolution_minority_ratio": 0.15,
        "resolution_minority_minutes": 10,
    }
    config = {
        "render_args": {"overlay": overlay},
        "common_event_args": {"auto_merge": True},
        "merge_args": {"merge_type": ["dm_video"]},
        "download_args": {"output_dir": str(tmp_path)},
    }
    handler = LiveEvents("test", config)
    handler.ended_dict["g"] = True
    handler.state_dict["g"] = []
    return handler


def video(path, group, segment, spans):
    return VideoInfo(
        path=str(path),
        group_id=group,
        segment_id=segment,
        duration=spans[-1][1],
        resolution=(spans[0][2], spans[0][3]),
        resolution_spans=spans,
        ctime=datetime.now(),
    )


def test_dispatches_minority_to_gpu_and_forces_dominant_canvas(tmp_path):
    handler = make_handler(tmp_path)
    first = video(tmp_path / "land.mp4", "g", 1, [(0, 700, 1920, 1080)])
    second = video(tmp_path / "port.mp4", "g", 2, [(0, 60, 1080, 1920)])
    handler.state_dict["g"] = [
        {
            "dm_video": {"status": "ready", "file": first, "wait": []},
            "src_video_pre": {"status": "ready", "file": first, "wait": []},
        },
        {
            "dm_video": {"status": "ready", "file": second, "wait": []},
            "src_video_pre": {"status": "ready", "file": second, "wait": []},
        },
    ]
    decision = handler._log_resolution_policy("g")
    messages = handler._dispatch_resolution_rerender("g", decision)
    assert decision["action"] == "re_render_minority"
    assert len(messages) == 1
    assert messages[0].data["mode"] == "gpu_render"
    assert messages[0].data["args"]["rescale_orientation"] == "landscape"
    assert messages[0].data["args"]["rescale_to_1080p"]
    assert messages[0].data["video"] is second
    assert handler.state_dict["g"][1]["dm_video"]["status"] == "rendering"


def test_failed_minority_rerender_restores_original_and_allows_merge(tmp_path, monkeypatch):
    handler = make_handler(tmp_path)
    dominant = video(tmp_path / "land.mp4", "g", 1, [(0, 700, 1920, 1080)])
    original = video(tmp_path / "port.mp4", "g", 2, [(0, 60, 1080, 1920)])
    handler.state_dict["g"] = [
        {
            "dm_video": {"status": "ready", "file": dominant, "wait": []},
            "src_video_pre": {"status": "ready", "file": dominant, "wait": []},
        },
        {
            "dm_video": {"status": "ready", "file": original, "wait": []},
            "src_video_pre": {"status": "ready", "file": original, "wait": []},
        }
    ]
    handler.session_data["g"] = {}
    handler.ended_dict["g"] = False
    monkeypatch.setattr(handler, "reconcile", lambda *args, **kwargs: [])
    decision = handler._log_resolution_policy("g")
    message = handler._dispatch_resolution_rerender("g", decision)[0]
    handler.onRenderError(
        PipeMessage(
            "render",
            "test",
            "error",
            request_id=message.request_id,
            msg="synthetic failure",
            data=RuntimeError("synthetic failure"),
        )
    )
    slot = handler.state_dict["g"][1]["dm_video"]
    assert slot["status"] == "ready"
    assert slot["file"].path == original.path
    assert handler.session_data["g"]["_resolution_rerender_failed"]
