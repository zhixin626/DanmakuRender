from DMR.Render.gpu_render import (
    EventScanner,
    parse_bitrate,
    resolve_output_fps,
    schedule_frames,
)
from DMR.Render.layout_models import PanelNode, RenderEvent


def test_schedule_25_to_50_repeats_each_source_frame_twice():
    source = [(index, index / 25) for index in range(3)]
    result = list(schedule_frames(iter(source[1:]), source[0], 50, source_fps=25))
    assert [item[0] for item in result] == [0, 0, 1, 1, 2, 2]


def test_schedule_rejects_invalid_fps():
    try:
        list(schedule_frames(iter([]), (0, 0), 0))
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")


def test_parse_bitrate_and_fixed_panel_shape():
    assert parse_bitrate("4M") == 4_000_000
    assert (
        PanelNode.from_mapping(
            {"type": "rect", "x": 0, "y": 0, "width": 10, "height": 10}
        ).width
        == 10
    )


def test_vfr_schedule_uses_latest_source_frame_and_tail():
    frames = [(f"f{index}", pts) for index, pts in enumerate((0.0, 0.071, 0.143))]
    result = list(schedule_frames(iter(frames[1:]), frames[0], 25, source_fps=25))
    assert [item[0] for item in result] == ["f0", "f0", "f1", "f1", "f2"]
    assert [round(item[1], 3) for item in result] == [0.0, 0.04, 0.08, 0.12, 0.16]


def test_scanline_enter_exit_and_source_order():
    events = [
        RenderEvent(
            "b",
            0,
            2,
            2,
            0.5,
            2.0,
            ((0.5, 5, 6), (1.0, 7, 8), (2.0, 5, 6)),
            {"enter_duration": 0.5, "exit_duration": 0.5, "outside_x": 5, "inside_x": 7},
        ),
        RenderEvent(
            "a",
            0,
            2,
            2,
            0.0,
            1.0,
            ((0.0, 1, 2), (1.0, 3, 4)),
            {"enter_duration": 0.0, "exit_duration": 0.0},
        ),
    ]
    scanner = EventScanner(events)
    visible = scanner.visible(0.75)
    assert [item[0].id for item in visible] == ["b", "a"]
    assert visible[0][3] == 128  # halfway between outside_x and inside_x
    assert scanner.visible(1.75)[0][0].id == "b"
    assert scanner.path_indices[0] == 1
    assert scanner.active_order == [(1, 0)]


def test_fixed_event_rejects_missing_fields():
    try:
        RenderEvent.from_mapping({"offset": 0})
    except KeyError as error:
        assert error.args[0] == "width"
    else:
        raise AssertionError("missing RenderEvent field must fail")


def test_frame_rate_and_encoder_argument_name_guard():
    assert resolve_output_fps(25.0, 50.0) == 50.0
    from unittest.mock import Mock
    from DMR.Render.gpu_render import create_encoder

    nvc = Mock()
    create_encoder(nvc, 1920, 1080, 60, "hevc_nvenc", {})
    assert nvc.CreateEncoder.call_args.kwargs == {"codec": "hevc", "fps": "60"}


def test_panel_carried_across_segments_does_not_refade():
    """A panel already inside at the cut keeps full opacity in both segments."""
    motion = {"enter_duration": 0.5, "exit_duration": 0.5, "outside_x": 2000, "inside_x": 1500}
    # Segment 1 ends while the panel is still fully inside.
    first = EventScanner([RenderEvent("g", 0, 2, 2, 0.0, 10.0,
                                      ((0.0, 2000, 900), (0.5, 1500, 900), (10.0, 1500, 900)), motion)])
    assert first.visible(9.9)[0][3] == 255
    # Segment 2 starts with the same panel clipped at t=0, then slides out.
    second = EventScanner([RenderEvent("g", 0, 2, 2, 0.0, 5.0,
                                       ((0.0, 1500, 900), (4.5, 1500, 900), (5.0, 2000, 900)), motion)])
    assert second.visible(0.0)[0][3] == 255
    assert second.visible(4.75)[0][3] == 128
