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


def test_holding_dm_does_not_block_src_merge(tmp_path):
    handler = make_handler(tmp_path)
    handler.config["merge_args"] = {"merge_type": ["src_video"]}
    source = video(tmp_path / "source.mp4", "g", 1, [(0, 700, 1920, 1080)])
    handler.state_dict["g"] = [
        {
            "src_video": {"status": "ready", "file": source, "wait": []},
            "src_video_pre": {"status": "ready", "file": source, "wait": []},
            "dm_video": {"status": "holding", "file": None, "wait": []},
        }
    ]
    messages = handler._check_for_merge("g")
    assert len(messages) == 1
    assert messages[0].target == "merger"


def test_multiple_rerenders_wait_until_last_callback(tmp_path, monkeypatch):
    handler = make_handler(tmp_path)
    dominant = video(tmp_path / "land.mp4", "g", 1, [(0, 700, 1920, 1080)])
    minority_a = video(tmp_path / "port_a.mp4", "g", 2, [(0, 60, 1080, 1920)])
    minority_b = video(tmp_path / "port_b.mp4", "g", 3, [(0, 60, 1080, 1920)])
    handler.state_dict["g"] = [
        {"dm_video": {"status": "ready", "file": dominant, "wait": []}, "src_video_pre": {"status": "ready", "file": dominant, "wait": []}},
        {"dm_video": {"status": "ready", "file": minority_a, "wait": []}, "src_video_pre": {"status": "ready", "file": minority_a, "wait": []}},
        {"dm_video": {"status": "ready", "file": minority_b, "wait": []}, "src_video_pre": {"status": "ready", "file": minority_b, "wait": []}},
    ]
    calls = []
    monkeypatch.setattr(handler, "reconcile", lambda *args, **kwargs: calls.append(args) or [])
    decision = handler._log_resolution_policy("g")
    messages = handler._dispatch_resolution_rerender("g", decision)
    assert len(messages) == 2
    handler.onRenderError(PipeMessage("render", "test", "error", request_id=messages[0].request_id, msg="bad", data=RuntimeError("bad")))
    assert not calls
    output = video(tmp_path / "rerendered.mp4", "g", 3, [(0, 60, 1920, 1080)])
    output.group_id = "g"
    handler.onRenderEnd(PipeMessage("render", "test", "end", request_id=messages[1].request_id, data={"config": {"mode": "gpu_render", "args": {"_resolution_rerender": True}}, "output": output}))
    assert calls
