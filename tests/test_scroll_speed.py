"""Scrolling paths must have one pixel speed, independent of canvas or text."""

import pytest

from DMR.Render.layout_compiler import LayoutCompiler, _position_at


def place(width, text_width, options, active=None, start=0):
    event = {
        "track": "r2l",
        "bounds": {"width": text_width},
        "path": [[start, 0, 0]],
    }
    compiler = LayoutCompiler({})
    accepted = compiler._place_r2l(
        event, active or [], width, 1080, {"fontsize": 36},
        {"vertical_ratio": 0.01, "horizontal_gap": 15, **options},
    )
    return accepted, event


@pytest.mark.parametrize("options,expected", [
    ({"speed": 240}, 240),
    ({"speed": 160}, 160),
    ({"speed": "300"}, 300),
])
def test_fixed_speed_for_orientations_and_text_lengths(options, expected):
    for canvas in (1080, 1920):
        for length in (40, 1500):
            accepted, event = place(canvas, length, options)
            assert accepted
            path = event["path"]
            x0, _ = _position_at(path, 0)
            x1, _ = _position_at(path, 1)
            assert x0 - x1 == pytest.approx(expected)
            assert path[-1][0] == pytest.approx((canvas + length) / expected)
            assert path[-1][1] == -length


@pytest.mark.parametrize("canvas", [1080, 1920])
def test_lane_gap_uses_fixed_speed(canvas):
    options = {"speed": 240}
    _, previous = place(canvas, 100, options)
    # One lane: a 100px sprite plus a 15px gap needs 115/240 seconds.
    assert not place(canvas, 900, options, [previous], 114 / 240)[0]
    assert place(canvas, 900, options, [previous], 116 / 240)[0]


@pytest.mark.parametrize("options", [
    {"speed": 0}, {"speed": -1}, {"speed": float("nan")},
    {"speed": float("inf")}, {"speed": None}, {},
])
def test_invalid_motion_configuration_fails(options):
    with pytest.raises(ValueError, match="r2l\\."):
        place(1080, 50, options)
