from DMR.Render.gpu_render import parse_bitrate, schedule_frame_times
from DMR.Render.plan_types import PanelNode


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
