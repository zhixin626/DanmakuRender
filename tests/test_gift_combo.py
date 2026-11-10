from DMR.Render.layout_compiler import merge_gift_records


def gift(line, when, uid, name="小心心", count=1, combo_id=None):
    return {
        "_line": line,
        "type": "gift",
        "received_at": when,
        "uid": uid,
        "uname": uid,
        "gift_name": name,
        "gift_count": count,
        "price_cny": 1,
        "combo_id": combo_id,
    }


def test_same_user_gift_merges_within_window_and_alternating_user_does_not():
    records = merge_gift_records(
        [gift(0, 0, "a"), gift(1, 5, "a", count=2), gift(2, 5, "b")],
        mode="total",
    )
    assert [(item["uid"], item["gift_count"]) for item in records] == [("a", 3), ("b", 1)]


def test_boundary_after_ten_seconds_starts_new_group():
    records = merge_gift_records([gift(0, 0, "a"), gift(1, 10.001, "a")])
    assert len(records) == 2


def test_combo_id_sums_each_message_and_keeps_live_states():
    records = merge_gift_records(
        [
            gift(0, 0, "a", count=1, combo_id="x"),
            gift(1, 2, "a", count=1, combo_id="x"),
        ],
        mode="live",
    )
    assert len(records) == 1
    assert records[0]["gift_count"] == 2
    assert len(records[0]["gift_states"]) == 2


def test_tid_deduplicates_only_when_tid_is_present():
    first = gift(0, 0, "a")
    duplicate = dict(first)
    first["tid"] = duplicate["tid"] = "same-message"
    records = merge_gift_records([first, duplicate])
    assert len(records) == 1
    assert records[0]["gift_count"] == 1
