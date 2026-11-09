"""Prepare the isolated two-hour final-acceptance sample and config copy."""

from __future__ import annotations

import json
import pathlib
import shutil
from collections import Counter

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE_JSONL = ROOT / "perf" / "samples" / "final_2h" / "少年不太冷_11月9日14点20分.jsonl"
CONFIG_SOURCE = ROOT.parent / "DanmakuRender" / "configs"
OUTPUT_JSONL = ROOT / "perf" / "samples" / "final_2h" / "少年不太冷_11月9日14点20分_augmented.jsonl"
CONFIG_DIR = ROOT / "perf" / "tmp" / "final_2h_config"


def main() -> None:
    """Add sparse coverage events across the full recording without touching source data."""
    metadata = {}
    events = []
    with SOURCE_JSONL.open(encoding="utf-8") as source:
        for line in source:
            record = json.loads(line)
            if record.get("type") == "meta":
                metadata = record
            elif record.get("received_at") is not None:
                events.append(record)
    start = float(metadata["segment_start_time"])
    vip_uid = "187495453"
    synthetic = []
    sc_prices = (0.5, 5, 20, 75, 300, 1200)
    gift_prices = (0.5, 5, 20, 75, 300, 1200)
    for batch, offset in enumerate((300, 1500, 2700, 3900, 5100, 6300)):
        for index, price in enumerate(sc_prices):
            synthetic.append({
                "type": "superchat",
                "received_at": start + offset + index,
                "uid": f"final-sc-{batch}-{index}",
                "uname": "两小时SC用户",
                "content": f"最终验收SC{index}😀" if index == 0 else f"最终验收SC{index}",
                "price": int(price * 10),
                "price_unit": "电池",
                "price_cny": price,
                "platform_duration": 5,
                "background_color": "#EDF5FF",
                "background_bottom_color": "#2A60B2",
                "name_color": "#666666",
                "content_color": "#A3F6FF",
                "_synthetic": "handwritten",
            })
        for index, price in enumerate(gift_prices):
            synthetic.append({
                "type": "gift",
                "received_at": start + offset + 20 + index,
                "uid": f"final-gift-{batch}-{index}",
                "uname": "两小时礼物用户",
                "gift_name": f"最终礼物{index}",
                "gift_count": 1,
                "gift_price": price * 10,
                "price_unit": "电池",
                "unit_price_cny": price,
                "price_cny": price,
                "content": f"送出 最终礼物{index}",
                "_synthetic": "handwritten",
            })
        for index in range(2):
            synthetic.append({
                "type": "member",
                "received_at": start + offset + 25 + index,
                "uid": f"final-member-{batch}-{index}",
                "uname": "两小时会员用户",
                "member_name": "舰长",
                "member_price": 138,
                "member_count": 1,
                "member_unit": "月",
                "price_cny": 138,
                "content": "开通 舰长(138元)×1月",
                "_synthetic": "modified",
            })
        synthetic.append({
            "type": "danmaku",
            "received_at": start + offset + 40,
            "uid": vip_uid,
            "uname": "VIP最终验收用户",
            "content": "VIP药丸 😀",
            "color": "ffffff",
            "_synthetic": "modified",
        })
    all_events = sorted(events + synthetic, key=lambda item: item["received_at"])
    with OUTPUT_JSONL.open("w", encoding="utf-8") as target:
        target.write(json.dumps(metadata, ensure_ascii=False) + "\n")
        for event in all_events:
            target.write(json.dumps(event, ensure_ascii=False) + "\n")

    if CONFIG_DIR.exists():
        shutil.rmtree(CONFIG_DIR)
    CONFIG_DIR.mkdir(parents=True)
    global_config = yaml.safe_load((CONFIG_SOURCE / "global.yml").read_text(encoding="utf-8"))
    global_config.setdefault("dmr_engine_args", {})["config_path"] = str(CONFIG_DIR)
    (CONFIG_DIR / "global.yml").write_text(yaml.safe_dump(global_config, allow_unicode=True, sort_keys=False), encoding="utf-8")
    shutil.copy2(CONFIG_SOURCE / "DMR-少年不太冷.yml", CONFIG_DIR / "DMR-少年不太冷.yml")
    counts = Counter(event.get("type") for event in all_events)
    manifest = ROOT / "perf" / "samples.md"
    with manifest.open("a", encoding="utf-8") as target:
        target.write("\n## final_2h/少年不太冷 11月9日14点20分\n")
        target.write(f"- 原始视频：D:\\DanmakuRender\\Tasks文件\\少年不太冷\\11月9日14点20分.flv\n- 原始弹幕：D:\\DanmakuRender\\Tasks文件\\少年不太冷\\11月9日14点20分.jsonl\n")
        target.write(f"- 隔离副本：{SOURCE_JSONL.parent}; 增强弹幕：{OUTPUT_JSONL}; 类型统计：{dict(counts)}\n")
        target.write("- 合成事件：每 20 分钟一批，SC/礼物各六档价格、会员共用 slide_right、VIP 药丸和 Emoji；0.5 元礼物按 min_price_cny=1 预期不显示。\n")
        target.write(f"- 三层配置副本：{CONFIG_DIR}；任务配置保留正式 VIP 用户 187495453/199828655/9881469。\n")
    print(json.dumps({"jsonl": str(OUTPUT_JSONL), "config": str(CONFIG_DIR), "counts": counts}, ensure_ascii=False))


if __name__ == "__main__":
    main()
