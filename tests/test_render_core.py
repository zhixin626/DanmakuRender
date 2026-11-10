from DMR.Render.gpu_render import (
    EventScanner,
    parse_bitrate,
    resolve_output_fps,
    schedule_frame_times,
    scheduled_frames,
)
from DMR.Render.plan_types import PanelNode, RenderEvent


def test_schedule_25_to_50_repeats_each_source_frame_twice():
    result = schedule_frame_times([0.0, 0.04, 0.08], 50)
    assert [item[0] for item in result] == [0, 0, 1, 1, 2, 2]


def test_schedule_rejects_invalid_fps():
    try:
        schedule_frame_times([0.0], 0)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")


def test_parse_bitrate_and_fixed_panel_shape():
    assert parse_bitrate("4M") == 4_000_000
    assert PanelNode.from_mapping({"type": "rect", "x": 0, "y": 0, "width": 10, "height": 10}).width == 10


def test_vfr_schedule_uses_latest_source_frame_and_tail():
    frames = [(f"f{index}", pts) for index, pts in enumerate((0.0, 0.071, 0.143))]
    result = list(scheduled_frames(iter(frames[1:]), frames[0], 25, target_duration=0.16))
    assert [item[0] for item in result] == ["f0", "f0", "f1", "f1", "f2"]
    assert [round(item[1], 3) for item in result] == [0.0, 0.04, 0.08, 0.12, 0.16]


def test_scanline_enter_exit_and_source_order():
    events = [
        RenderEvent("b", 0, 2, 2, 0.5, 2.0, ((0.5, 5, 6), (2.0, 5, 6)), {"enter_duration": 0.5, "exit_duration": 0.5}),
        RenderEvent("a", 0, 2, 2, 0.0, 1.0, ((0.0, 1, 2), (1.0, 3, 4)), {"enter_duration": 0.0, "exit_duration": 0.0}),
    ]
    scanner = EventScanner(events)
    visible = scanner.visible(0.75)
    assert [item[0].id for item in visible] == ["b", "a"]
    assert visible[0][3] == 128
    assert scanner.visible(1.75)[0][0].id == "b"


def test_fixed_event_rejects_missing_fields():
    try:
        RenderEvent.from_mapping({"offset": 0})
    except KeyError as error:
        assert error.args[0] == "width"
    else:
        raise AssertionError("missing RenderEvent field must fail")


def test_frame_rate_and_encoder_argument_name_guard():
    assert resolve_output_fps(25.0, 50.0) == 50.0
    source = __import__("pathlib").Path("DMR/Render/gpu_render.py").read_text(encoding="utf-8")
    assert "fps=str(frame_rate)" in source
    assert "framerate=frame_rate" not in source
