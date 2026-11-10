from DMR.Render.resolution import (
    ResolutionSpan,
    decide_session_parts,
    normalize_resolution_spans,
)


def test_startup_instability_and_short_switch_are_filtered():
    spans = normalize_resolution_spans(
        [(0, 30, 1080, 1920), (30, 150, 1920, 1080), (150, 170, 1080, 1920)]
    )
    assert [(item.start, item.end, item.orientation) for item in spans] == [
        (30.0, 150.0, "landscape")
    ]


def test_short_minority_is_rerendered_to_one_video():
    result = decide_session_parts(
        [ResolutionSpan(0, 600, 1920, 1080), ResolutionSpan(600, 660, 1080, 1920)]
    )
    assert result["dominant"] == "landscape"
    assert result["action"] == "re_render_minority"
    assert result["canvas"] == (1920, 1080)


def test_long_orientation_switch_becomes_consecutive_parts():
    result = decide_session_parts(
        [ResolutionSpan(0, 900, 1920, 1080), ResolutionSpan(900, 1800, 1080, 1920)]
    )
    assert result["action"] == "parts"
    assert [run["orientation"] for run in result["runs"]] == ["landscape", "portrait"]


def test_forced_orientation_preserves_exception_policy():
    result = decide_session_parts(
        [ResolutionSpan(0, 100, 1920, 1080), ResolutionSpan(100, 200, 1080, 1920)],
        forced_orientation="portrait",
    )
    assert result["dominant"] == "portrait"
    assert result["canvas"] == (1080, 1920)


def test_short_switch_in_session_timeline_is_filtered():
    result = decide_session_parts(
        [
            ResolutionSpan(0, 100, 1920, 1080),
            ResolutionSpan(100, 120, 1080, 1920),
            ResolutionSpan(120, 220, 1920, 1080),
        ],
        ignore_head_seconds=0,
        min_span_seconds=30,
    )
    assert result["action"] == "single"
    assert result["minority_seconds"] == 0
