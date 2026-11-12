"""Real user failure plus independent partial/colored loss regression cases."""

import json
from pathlib import Path

import numpy as np
from PIL import Image

from DMR.Render.sprites import build_atlas
from tests.render_checks import OverlaySignal, find_dropouts


def test_user_video_dropout_is_detected_but_fixed_frames_are_not():
    folder = Path(__file__).parent / "references/flicker"
    plan = json.loads((folder / "plan.json").read_text(encoding="utf8"))
    atlas = build_atlas(plan)
    reports = {}
    for name in ("broken", "fixed"):
        detector = OverlaySignal(atlas, (1080, 1920), (270, 480))
        measurements = []
        for index in range(3):
            source = np.asarray(Image.open(folder / f"source_{index}.png")).astype(
                np.float32
            )
            output = np.asarray(Image.open(folder / f"{name}_{index}.png")).astype(
                np.float32
            )
            seconds = 1.12 + index / 50
            measurements.append(
                {
                    "frame": index,
                    "seconds": seconds,
                    "signals": detector.measure(source, output, seconds),
                }
            )
        reports[name] = find_dropouts(measurements)
    assert any(item["frame"] == 1 for item in reports["broken"])
    assert reports["fixed"] == []


def test_surviving_overlay_cannot_hide_another_events_multi_frame_loss():
    frames = [
        {
            "frame": i,
            "seconds": i / 50,
            "signals": [
                {"event": "missing", "score": 0.02 if i in (2, 3) else 0.9},
                {"event": "surviving", "score": 1.0},
            ],
        }
        for i in range(6)
    ]
    assert {item["frame"] for item in find_dropouts(frames)} == {2, 3}


def test_rgb_signal_detects_chroma_only_loss():
    # An isoluminant colored overlay must still have measurable RGB signal.
    atlas = {
        "events": [
            {
                "id": "color",
                "offset": 0,
                "width": 10,
                "height": 10,
                "start": 0,
                "end": 3,
                "path": [[0, 5, 5], [3, 5, 5]],
                "motion": {},
            }
        ],
        "y": np.full(100, 128, np.uint8),
        "u": np.full(100, 200, np.uint8),
        "v": np.full(100, 60, np.uint8),
        "a": np.full(100, 255, np.uint8),
    }
    source = np.full((20, 20, 3), 130, np.float32)
    detector = OverlaySignal(atlas, (20, 20), (20, 20))
    (signal,) = detector.measure(source, source.copy(), 1)
    assert signal["score"] == 0


def test_long_missing_run_includes_interior_frames():
    measurements = [
        {
            "frame": i,
            "seconds": i / 50,
            "signals": [{"event": "lost", "score": 1 if i in (0, 15) else 0}],
        }
        for i in range(16)
    ]
    assert {item["frame"] for item in find_dropouts(measurements)} == set(range(1, 15))


def test_a_later_panel_excludes_occluded_lower_layer_pixels():
    events = [
        {
            "id": name,
            "offset": i * 100,
            "width": 10,
            "height": 10,
            "start": 0,
            "end": 3,
            "path": [[0, 5, 5], [3, 5, 5]],
            "motion": {},
        }
        for i, name in enumerate(("lower", "panel"))
    ]
    atlas = {
        "events": events,
        "y": np.full(200, 210, np.uint8),
        "u": np.full(200, 128, np.uint8),
        "v": np.full(200, 128, np.uint8),
        "a": np.r_[np.full(100, 255, np.uint8), np.full(100, 180, np.uint8)],
    }
    detector = OverlaySignal(atlas, (20, 20), (20, 20))
    source = np.full((20, 20, 3), 40, np.float32)
    scores = detector.measure(source, source.copy(), 1)
    assert "lower" not in {item["event"] for item in scores}
