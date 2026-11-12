import json
from pathlib import Path

import pytest
from PIL import features

from DMR.Downloader.Danmaku.event_writer import EventWriter
from DMR.Render.layout_compiler import LayoutCompiler, clean_text, _hex_color


def test_invalid_event_color_falls_back_to_default():
    assert _hex_color("zzzzzz", "#FFFFFF") == "#FFFFFF"
    assert _hex_color("123456", "#FFFFFF") == "#123456"


def test_entry_events_are_dropped_by_default(tmp_path):
    events = tmp_path / "entry.jsonl"
    events.write_text(
        "\n".join(
            [
                json.dumps({"type": "meta", "schema": 1, "platform": "bilibili", "segment_start_time": 100}),
                json.dumps({"type": "entry", "received_at": 100, "uid": "1", "uname": "entry", "content": "进入"}),
            ]
        ),
        encoding="utf8",
    )
    config = {"r2l": {"speed": 240, "danmaku": {"font": "Microsoft YaHei", "fontsize": 30}}, "slide_left": {}, "slide_right": {}}
    video = VideoInfo(path="source.flv", dm_file_id=str(events), duration=20, resolution=(1920, 1080))
    compiled = LayoutCompiler(config, engine="gpu_render", engine_config=config).compile(
        video=video, events_path=str(events), work_dir=str(tmp_path)
    )
    try:
        assert compiled.event_count == 0
        assert compiled.dropped_count == 1
    finally:
        compiled.cleanup()
from DMR.Render.layout_models import RenderPlan
from DMR.Render.sprites import SpriteFactory
from DMR.utils import VideoInfo


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("a\u2028b\u2029c\r\n\t\v\f\x1c\x1d\x1e\x85d", "a b c d"),
        ("a\x00b\x7fc\x80d", "abcd"),
        ("\u200b\u2060\ufeff\u180e\u00ade", "e"),
        ("  a\u00a0\u3000\u3164  b  ", "a b"),
    ],
)
def test_clean_text_normalizes_controls_and_spaces(raw, expected):
    assert clean_text(raw) == expected


def test_user_reported_name_is_cleaned_to_single_space():
    assert clean_text("安心    ㅤ       ㅤ:") == "安心 :"


def test_clean_text_preserves_emoji_sequences_and_combining_marks():
    value = "👨‍👩‍👧 ❤️ 🏳️‍🌈 👍🏽 Ấ กิ่ Z͑͗"
    assert clean_text(value) == value


def test_bidi_controls_are_removed_without_raqm():
    value = "abc\u202ede\u202c"
    cleaned = clean_text(value)
    if features.check("raqm"):
        assert cleaned == "abcde"
    else:
        assert cleaned == "abcde"


def test_event_writer_escapes_line_separator(tmp_path):
    path = tmp_path / "events.jsonl"
    writer = EventWriter("bilibili", ["danmaku"])
    writer.open(str(path), 100)
    writer._write_line({"type": "danmaku", "content": "a\u2028b\u2029c"})
    writer.close()
    raw = path.read_text(encoding="utf8")
    assert "\\u2028" in raw and "\\u2029" in raw
    assert json.loads(raw.splitlines()[1])["content"] == "a\u2028b\u2029c"


def test_invisible_uname_falls_back_to_uid_and_empty_content_drops(tmp_path):
    events = tmp_path / "events.jsonl"
    rows = [
        {"type": "meta", "schema": 1, "platform": "bilibili", "segment_start_time": 100},
        {"type": "danmaku", "received_at": 100, "uid": "604006167", "tid": "  same  ", "uname": "\u3164\u200b", "content": "保留"},
        {"type": "danmaku", "received_at": 101, "uid": "2", "uname": "用户", "content": "\u2028\u200b"},
    ]
    events.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf8")
    config = {
        "r2l": {"speed": 240, "danmaku": {"font": "Microsoft YaHei", "fontsize": 30, "vip_users": {"604006167": None}}},
        "slide_left": {},
        "slide_right": {},
    }
    video = VideoInfo(path="source.flv", dm_file_id=str(events), duration=20, resolution=(1920, 1080))
    compiled = LayoutCompiler(config, engine="gpu_render", engine_config=config).compile(
        video=video, events_path=str(events), work_dir=str(tmp_path)
    )
    try:
        _, records = LayoutCompiler._read_events(str(events))
        assert records[0]["tid"] == "  same  "
        plan = RenderPlan.load(compiled.render_plan_path)
        lines = [event for event in plan.events if event["track"] == "r2l"]
        assert len(lines) == 1
        assert lines[0]["visual"]["text"].startswith("604006167:")
    finally:
        compiled.cleanup()


def test_scroll_sprite_margin_does_not_change_measurement():
    factory = SpriteFactory("C:/Windows/Fonts/msyhbd.ttc", "C:/Windows/Fonts/seguiemj.ttf", {}, 36)
    plain = factory.get_sprite("Ấ กิ่", (255, 255, 255), scroll_vertical_gap=0)
    padded = factory.get_sprite("Ấ กิ่", (255, 255, 255), scroll_vertical_gap=10)
    assert padded[5] > plain[5]
    assert padded[4] == plain[4]
    assert padded[9] > plain[9]


def test_scroll_sprite_margin_is_half_the_configured_gap():
    factory = SpriteFactory("C:/Windows/Fonts/msyhbd.ttc", "C:/Windows/Fonts/seguiemj.ttf", {}, 36)
    no_gap = factory.get_sprite("Ấ", (255, 255, 255), scroll_vertical_gap=0)
    ten_px = factory.get_sprite("Ấ", (255, 255, 255), scroll_vertical_gap=10)
    assert no_gap[5] < ten_px[5]
    assert ten_px[5] - no_gap[5] == 10
