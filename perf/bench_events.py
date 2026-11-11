"""Build the fixed-seed 10c coverage JSONL without touching Tasks input."""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path


def build(source: Path, output: Path, seed: int = 20261112) -> None:
    random.seed(seed)
    lines = [
        json.loads(line)
        for line in source.read_text(encoding="utf-8").split("\n")
        if line.strip()
    ]
    meta = lines[0]
    start = float(meta["segment_start_time"])
    synthetic: list[dict] = []

    def add(offset: float, event: dict, scenario: str) -> None:
        event.update(received_at=start + offset, _synthetic=True, _scenario=scenario)
        synthetic.append(event)

    texts = [
        "A1 short", "字", "超长弹幕" * 40,
        "👨‍👩‍👧 🏳️‍🌈 👍🏽 🇨🇳 ❤️", "[doge] 混排", "[不存在的表情]",
        "安心    ㅤ       ㅤ:", "line\u2028one", "Tiếng Việt Ấ", "กิ่", "안녕하세요",
        "مرحبا", "שלום", "Привет", "こんにちは", "★☆♪✿㊗ 𝓐𝓑",
    ]
    for index, text in enumerate(texts):
        add(20 + index * 4, {
            "type": "danmaku", "uid": str(900000 + index),
            "uname": f"bench-{index}", "content": text,
            "color": random.choice(["ffffff", "000000", "ff0000", "zzzzzz"]),
        }, "A1-A4/E1-E6")
    for index in range(150):
        add(120 + index * 0.01, {
            "type": "danmaku", "uid": f"burst-{index}", "uname": "burst",
            "content": f"A5-{index}", "color": "ffffff",
        }, "A5")
    for index in range(50):
        add(130, {
            "type": "danmaku", "uid": f"same-{index}", "uname": "same-ts",
            "content": f"same timestamp {index}", "color": "ffffff",
        }, "A5")
    for index, uid in enumerate(("41694436", "31831236", "324941243")):
        add(200 + index * 3, {
            "type": "danmaku", "uid": uid, "uname": f"VIP-{uid}",
            "content": "VIP " + ("超长" * 50 if index == 2 else "pill"),
            "color": "ffffff",
        }, "A6")
    for index, amount in enumerate((0.5, 5, 30, 60, 500, 2000)):
        add(300 + index * 5, {
            "type": "superchat", "uid": f"sc-{index}", "uname": f"SC-{index}",
            "content": "SC content " + "中英文 mixed " * (index + 1),
            "price_cny": amount, "color": "ffffff",
        }, "B1-B5")
    for index, (offset, count) in enumerate(((400, 1), (401, 1), (405, 1), (410.1, 1), (410.1, 10))):
        add(offset, {
            "type": "gift", "uid": "combo-user", "uname": "combo-user",
            "gift_name": "小心心" if index < 4 else "辣条", "gift_count": count,
            "price_cny": 1, "unit_price_cny": 1, "tid": "combo-tid" if index == 2 else f"combo-{index}",
            "content": "送出 礼物",
        }, "C1-C4")
    for index, level in enumerate(("舰长", "提督", "总督")):
        add(500 + index * 2, {
            "type": "member", "uid": f"member-{index}", "uname": f"member-{index}",
            "member_name": level, "member_count": (1, 3, 12)[index],
        }, "D1")
    # Remaining 10c functional cases: missing fields, filters, entry, panel
    # wrapping/stacking, all gift boundary conditions, controls, ordering and
    # exact duplicates. These are deliberately separate from performance data.
    add(90, {"type": "danmaku", "uid": "a2-missing-color", "uname": "A2", "content": "missing color"}, "A2")
    add(100, {"type": "danmaku", "uid": "a7-keyword", "uname": "ok", "content": "FILTERME keyword"}, "A7")
    add(100.1, {"type": "danmaku", "uid": "blocked-user", "uname": "blocked-user", "content": "filtered user"}, "A7")
    add(110, {"type": "entry", "uid": "entry-user", "uname": "entry-user", "content": "进入直播间"}, "A8")
    add(140, {"type": "danmaku", "content": "missing uname"}, "A9")
    add(144, {"type": "danmaku", "uname": "missing uid", "content": "missing uid"}, "A9")
    add(148, {"type": "danmaku", "uid": "empty", "uname": "empty", "content": ""}, "A9")
    add(152, {"type": "danmaku", "uid": "null", "uname": "null", "content": None}, "A9")
    add(310, {"type": "superchat", "uid": "sc-long", "uname": "SC" * 100, "content": "第一行\n第二行\n第三行\n第四行\n" + "word" * 80 + "，标点", "price_cny": 30}, "B2")
    add(320, {"type": "superchat", "uid": "sc-emoji", "uname": "SC-emoji", "content": "👨‍👩‍👧 [doge] mixed", "price_cny": 60, "emoticon": {"name": "doge", "url": "", "emoticon_unique": "doge"}}, "B3")
    for index in range(6):
        add(330, {"type": "superchat", "uid": f"sc-stack-{index}", "uname": f"stack-{index}", "content": f"same timestamp SC {index}", "price_cny": 5 + index}, "B4")
    for index in range(3):
        add(345 + index * 0.1, {"type": "superchat", "uid": f"sc-exit-{index}", "uname": f"exit-{index}", "content": "same exit", "price_cny": 5, "duration": 1}, "B4")
    add(120.2, {"type": "superchat", "uid": "sc-overlap", "uname": "overlap", "content": "SC with A5", "price_cny": 5}, "B5")
    for index, price in enumerate((0, 1, 10, 50, 100, 1000)):
        add(350 + index * 5, {"type": "gift", "uid": f"price-{index}", "uname": f"price-{index}", "gift_name": "小心心", "gift_count": 1, "price_cny": price, "unit_price_cny": price, "content": "price boundary"}, "C1")
    add(385, {"type": "gift", "uid": "missing-price", "uname": "missing-price", "gift_name": "小心心", "gift_count": 1, "content": "missing price"}, "C4")
    add(390, {"type": "gift", "uid": "missing-count", "uname": "missing-count", "gift_name": "小心心", "price_cny": 1, "content": "missing count"}, "C4")
    for index, offset in enumerate((420, 420.2, 421, 426, 431.1, 435, 440, 445, 450, 455, 460, 465)):
        add(offset, {"type": "gift", "uid": f"combo-{index % 3}", "uname": f"combo-{index % 3}", "gift_name": ("辣条" if index % 2 else "小心心"), "gift_count": (10 if index == 6 else 1), "price_cny": (100 if index == 9 else 1), "unit_price_cny": 1, "tid": f"c2-{index if index != 8 else 7}", "content": f"combo scenario {index}"}, "C2")
    for index in range(4):
        add(470, {"type": "gift", "uid": f"exit-gift-{index}", "uname": f"exit-gift-{index}", "gift_name": "小心心", "gift_count": 1, "price_cny": 1, "duration": 1, "content": "exit"}, "C3")
    add(500, {"type": "gift", "uid": "same-time-gift", "uname": "same-time-gift", "gift_name": "小心心", "gift_count": 1, "price_cny": 1, "content": "member overlap"}, "D1")
    controls = "zero\x00bell\x07esc\x1b\u2029tab\tcr\r\n"
    add(520, {"type": "danmaku", "uid": "controls", "uname": "\u200b\u2060\ufeff", "content": controls}, "E2-E3")
    add(530, {"type": "danmaku", "uid": "zalgo", "uname": "zalgo", "content": "Z͑͗a̐l̄ḡō e\u0301"}, "E4")
    add(540, {"type": "superchat", "uid": "sc-special", "uname": "安心ㅤمرحبا", "content": "special SC", "price_cny": 5}, "E6")
    add(541, {"type": "gift", "uid": "gift-special", "uname": "安心ㅤمرحبا", "gift_name": "小心心", "gift_count": 1, "price_cny": 1, "content": "special gift"}, "E6")
    add(0, {"type": "danmaku", "uid": "zero-time", "uname": "zero", "content": "exact zero"}, "F1")
    add(600, {"type": "danmaku", "uid": "six-hundred", "uname": "600", "content": "exact 600"}, "F1")
    add(570, {"type": "danmaku", "uid": "late-first", "uname": "late", "content": "written first"}, "F2")
    add(560, {"type": "danmaku", "uid": "early-second", "uname": "early", "content": "written second"}, "F2")
    duplicate = {"type": "danmaku", "uid": "duplicate", "uname": "duplicate", "content": "same row"}
    add(580, dict(duplicate), "F3")
    add(580, dict(duplicate), "F3")
    # Keep the original records byte-for-byte represented and append only synthetic rows.
    output.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in [*lines, *synthetic]) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--seed", type=int, default=20261112)
    args = parser.parse_args()
    build(args.source, args.output, args.seed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
