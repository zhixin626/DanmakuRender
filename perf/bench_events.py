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
