"""Prepare a full-coverage sample and an isolated test configuration."""

from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import subprocess
from collections import Counter

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
TASKS = ROOT.parent / "DanmakuRender" / "Tasks文件"
SOURCE_VIDEO = TASKS / "峰哥" / "8月18日14点19分.flv"
SOURCE_JSONL = TASKS / "峰哥" / "8月18日14点19分.jsonl"
START_SECONDS = 1860
DURATION_SECONDS = 300


def read_events(path: pathlib.Path) -> tuple[dict, list[dict]]:
    """Read JSONL metadata and event records without changing the source."""
    metadata = {}
    events = []
    with path.open(encoding="utf-8") as source:
        for line in source:
            record = json.loads(line)
            if record.get("type") == "meta":
                metadata = record
            elif "received_at" in record:
                events.append(record)
    return metadata, events


def synthetic_gifts(segment_start: float) -> list[dict]:
    """Add one event in every configured gift price tier when real data lacks it."""
    prices = (0.5, 5.0, 20.0, 75.0, 300.0, 1200.0)
    result = []
    for index, price in enumerate(prices):
        result.append({
            "type": "gift",
            "received_at": segment_start + 20 + index,
            "uid": f"synthetic-gift-{index}",
            "uname": "覆盖测试用户",
            "gift_name": f"覆盖礼物{index + 1}",
            "gift_count": 1,
            "gift_price": price * 10,
            "price_unit": "电池",
            "unit_price_cny": price,
            "price_cny": price,
            "content": f"送出 覆盖礼物{index + 1}(测试)",
            "_synthetic": "handwritten",
        })
    return result


def write_config_copy(destination: pathlib.Path, vip_uid: str) -> pathlib.Path:
    """Copy the 峰哥 task config and add VIP only in the test copy."""
    destination.mkdir(parents=True, exist_ok=True)
    global_path = destination / "global.yml"
    task_path = destination / "DMR-峰哥.yml"
    global_config = yaml.safe_load((ROOT.parent / "DanmakuRender" / "configs" / "global.yml").read_text(encoding="utf-8"))
    global_config.setdefault("dmr_engine_args", {})["config_path"] = str(destination)
    global_config.setdefault("render_args", {}).setdefault("overlay", {}).setdefault("r2l", {}).setdefault("danmaku", {})["vip_users"] = {str(vip_uid): None}
    global_config.pop("_test_only", None)
    global_path.write_text(yaml.safe_dump(global_config, allow_unicode=True, sort_keys=False), encoding="utf-8")
    shutil.copy2(ROOT.parent / "DanmakuRender" / "configs" / "DMR-峰哥.yml", task_path)
    return global_path


def main() -> None:
    """Create the full_coverage sample, config copy, and manifest entry."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=pathlib.Path, default=ROOT / "perf" / "samples" / "full_coverage")
    parser.add_argument("--config-output", type=pathlib.Path, default=ROOT / "perf" / "tmp" / "full_coverage_config")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    metadata, events = read_events(SOURCE_JSONL)
    source_start = float(metadata["segment_start_time"])
    selected = [event for event in events if START_SECONDS <= event["received_at"] - source_start < START_SECONDS + DURATION_SECONDS]
    selected_start = source_start + START_SECONDS
    synthetic = synthetic_gifts(selected_start)
    all_events = selected + synthetic
    all_events.sort(key=lambda event: event["received_at"])
    output_video = args.output / "峰哥_8月18日14点19分_1860-2160.flv"
    output_jsonl = args.output / "峰哥_8月18日14点19分_1860-2160_augmented.jsonl"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(START_SECONDS), "-i", str(SOURCE_VIDEO), "-t", str(DURATION_SECONDS), "-c", "copy", str(output_video)], check=True)
    with output_jsonl.open("w", encoding="utf-8") as target:
        target.write(json.dumps({**metadata, "segment_start_time": selected_start}, ensure_ascii=False) + "\n")
        for event in all_events:
            target.write(json.dumps(event, ensure_ascii=False) + "\n")
    vip_uid = Counter(event["uid"] for event in selected if event.get("type") == "danmaku").most_common(1)[0][0]
    config_path = write_config_copy(args.config_output, vip_uid)
    counts = Counter(event.get("type") for event in all_events)
    counts["emoji_or_emoticon"] = sum(bool(event.get("emoticon")) or any(char in event.get("content", "") for char in "😀😂🤣😅🥹") or "[" in event.get("content", "") for event in all_events)
    manifest = ROOT / "perf" / "samples.md"
    with manifest.open("a", encoding="utf-8") as target:
        target.write("\n## full_coverage/峰哥 8月18日14点19分\n")
        target.write(f"- 原始视频：{SOURCE_VIDEO}\n- 原始弹幕：{SOURCE_JSONL}\n- 截取：{START_SECONDS}s–{START_SECONDS + DURATION_SECONDS}s；输出：{output_video}\n")
        target.write(f"- 增强弹幕：{output_jsonl}；真实事件数量：{len(selected)}；总事件数量：{len(all_events)}；类型：{dict(counts)}\n")
        target.write(f"- 测试配置副本：{config_path}；VIP 用户：{vip_uid}（原配置无 vip_users，未修改正式配置）\n")
        target.write("- 合成事件：6 个 handwritten 礼物，价格 0.5/5/20/75/300/1200 元，集中在片段 20–25 秒，用于覆盖价格持续时间/颜色档位和重叠进出。\n")
    print(json.dumps({"video": str(output_video), "jsonl": str(output_jsonl), "config": str(config_path), "vip_uid": vip_uid, "counts": counts}, ensure_ascii=False))


if __name__ == "__main__":
    main()
