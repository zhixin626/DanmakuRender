from DMR.Render.layout_compiler import merge_gift_records
from DMR.Render.layout_compiler import LayoutCompiler
from DMR.Render.layout_models import RenderPlan
from DMR.utils import VideoInfo
import json
from pathlib import Path
import yaml


def gift(line, when, uid, name="小心心", count=1, tid=None):
    return {
        "_line": line,
        "type": "gift",
        "received_at": when,
        "uid": uid,
        "uname": uid,
        "gift_name": name,
        "gift_count": count,
        "price_cny": 1,
        "tid": tid,
    }


def test_same_user_gift_merges_within_window_and_alternating_user_does_not():
    records = merge_gift_records(
        [gift(0, 0, "a"), gift(1, 5, "a", count=2), gift(2, 5, "b")],
    )
    assert [(item["uid"], item["gift_count"]) for item in records] == [("a", 3), ("b", 1)]


def test_boundary_after_five_seconds_starts_new_group():
    records = merge_gift_records([gift(0, 0, "a"), gift(1, 5, "a")])
    assert len(records) == 1
    records = merge_gift_records([gift(0, 0, "a"), gift(1, 5.001, "a")])
    assert len(records) == 2


def test_states_keep_cumulative_count_and_compact_shape():
    records = merge_gift_records(
        [
            gift(0, 0, "a", count=1),
            gift(1, 2, "a", count=1),
            gift(2, 4, "a", count=1),
        ]
    )
    assert len(records) == 1
    assert records[0]["gift_count"] == 3
    assert records[0]["gift_states"] == [(0.0, 1, 1.0), (2.0, 2, 1.0), (4.0, 3, 1.0)]


def test_tid_deduplicates_only_when_tid_is_present():
    first = gift(0, 0, "a")
    duplicate = dict(first)
    first["tid"] = duplicate["tid"] = "same-message"
    records = merge_gift_records([first, duplicate])
    assert len(records) == 1
    assert records[0]["gift_count"] == 1


def test_active_gift_state_moves_up_when_later_panel_arrives(tmp_path):
    events = tmp_path / "events.jsonl"
    rows = [{"type": "meta", "schema": 1, "platform": "bilibili", "segment_start_time": 100}]
    for line, when, uid in ((2, 100, "a"), (3, 103, "a"), (4, 106, "a"), (5, 110, "b")):
        rows.append(
            {
                "type": "gift",
                "received_at": when,
                "uid": uid,
                "uname": uid,
                "gift_name": "小心心",
                "gift_count": 1,
                "price_cny": 1,
                "_line": line,
            }
        )
    events.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf8")
    config = yaml.safe_load(Path("DMR/Config/default.yml").read_text(encoding="utf8"))["render_args"]["overlay"]
    config["slide_right"]["gift"]["combo_window_seconds"] = 5
    video = VideoInfo(path="source.flv", dm_file_id=str(events), duration=20, resolution=(1080, 1920))
    compiled = LayoutCompiler(config, engine="gpu_render", engine_config=config).compile(
        video=video, events_path=str(events), work_dir=str(tmp_path)
    )
    try:
        plan = RenderPlan.load(compiled.render_plan_path)
        states = [
            event for event in plan.events if event.get("gift_state_group", "").endswith(":2")
        ]
        assert len(states) == 3
        assert [states[i]["path"][-1][0] for i in range(2)] == [
            states[i + 1]["path"][0][0] for i in range(2)
        ]
        assert len({event["bounds"]["width"] for event in states}) == 1
        assert states[-1]["path"][-1][2] < states[-1]["path"][0][2]
    finally:
        compiled.cleanup()


def test_interleaved_gift_state_keeps_slot_at_number_change(tmp_path):
    events = tmp_path / "events.jsonl"
    rows = [{"type": "meta", "schema": 1, "platform": "bilibili", "segment_start_time": 100}]
    for line, when, uid in ((2, 100, "a"), (3, 102, "b"), (4, 103, "a")):
        rows.append(
            {
                "type": "gift",
                "received_at": when,
                "uid": uid,
                "uname": uid,
                "gift_name": "小心心",
                "gift_count": 1,
                "price_cny": 1,
                "_line": line,
            }
        )
    events.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf8")
    config = yaml.safe_load(Path("DMR/Config/default.yml").read_text(encoding="utf8"))["render_args"]["overlay"]
    video = VideoInfo(path="source.flv", dm_file_id=str(events), duration=20, resolution=(1080, 1920))
    compiled = LayoutCompiler(config, engine="gpu_render", engine_config=config).compile(
        video=video, events_path=str(events), work_dir=str(tmp_path)
    )
    try:
        plan = RenderPlan.load(compiled.render_plan_path)
        states = [
            event for event in plan.events if event.get("gift_state_group", "").endswith(":2")
        ]
        assert len(states) == 2
        assert states[0]["path"][-1][2] == states[1]["path"][0][2]
    finally:
        compiled.cleanup()


def test_gift_state_survives_segment_boundary_without_overlap(tmp_path):
    config = yaml.safe_load(Path("DMR/Config/default.yml").read_text(encoding="utf8"))["render_args"]["overlay"]
    first_events = tmp_path / "first.jsonl"
    second_events = tmp_path / "second.jsonl"
    first_events.write_text(
        "\n".join(
            json.dumps(row, ensure_ascii=False)
            for row in [
                {"type": "meta", "schema": 1, "platform": "bilibili", "segment_start_time": 100},
                {"type": "gift", "received_at": 100, "uid": "a", "uname": "a", "gift_name": "小心心", "gift_count": 1, "price_cny": 1},
            ]
        ),
        encoding="utf8",
    )
    second_events.write_text(
        "\n".join(
            json.dumps(row, ensure_ascii=False)
            for row in [
                {"type": "meta", "schema": 1, "platform": "bilibili", "segment_start_time": 110},
                {"type": "gift", "received_at": 110, "uid": "b", "uname": "b", "gift_name": "小心心", "gift_count": 1, "price_cny": 1},
            ]
        ),
        encoding="utf8",
    )
    video = VideoInfo(path="source.flv", dm_file_id=str(first_events), duration=10, resolution=(1080, 1920))
    first = LayoutCompiler(config, engine="gpu_render", engine_config=config).compile(
        video=video, events_path=str(first_events), work_dir=str(tmp_path)
    )
    try:
        video.dm_file_id = str(second_events)
        second = LayoutCompiler(config, engine="gpu_render", engine_config=config).compile(
            video=video,
            events_path=str(second_events),
            previous_state=first.next_state,
            work_dir=str(tmp_path),
        )
        try:
            plan = RenderPlan.load(second.render_plan_path)
            panels = [event for event in plan.events if event["track"] == "slide_right"]
            assert len(panels) >= 2
            assert all(event["path"][0][2] != 0 for event in panels)
            assert len({event["path"][-1][2] for event in panels}) == len(panels)
        finally:
            second.cleanup()
    finally:
        first.cleanup()
