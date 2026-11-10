"""Prepare a full-coverage sample and an isolated test configuration."""

from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import subprocess
import sys
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
    """Add gift tiers, including a below-threshold negative control."""
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


def synthetic_superchats(segment_start: float) -> list[dict]:
    """Create a dense SC burst covering every duration and color tier."""
    prices = (0.5, 5.0, 20.0, 75.0, 300.0, 1200.0)
    result = []
    for index, price in enumerate(prices):
        result.append({
            "type": "superchat",
            "received_at": segment_start + 30 + index,
            "uid": f"synthetic-sc-{index}",
            "uname": "SC覆盖测试用户",
            "content": f"覆盖SC{index + 1}" + ("😀" if index == 0 else "[dog]" if index == 1 else ""),
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
    return result


def synthetic_members(segment_start: float) -> list[dict]:
    """Add members beside gifts to exercise the shared slide_right queue."""
    result = []
    for index, offset in enumerate((22, 24)):
        result.append({
            "type": "member",
            "received_at": segment_start + offset,
            "uid": f"synthetic-member-{index}",
            "uname": "会员覆盖测试用户",
            "member_name": "舰长",
            "member_price": 138,
            "member_count": 1,
            "member_unit": "月",
            "price_cny": 138,
            "content": "开通 舰长(138元)×1月",
            "_synthetic": "modified",
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


def write_resolved_config(global_path: pathlib.Path, destination: pathlib.Path) -> pathlib.Path:
    """Persist the exact three-layer merged task config used by the renderer."""
    sys.path.insert(0, str(ROOT))
    from DMR.Config import Config

    config = Config(str(global_path))
    resolved = config.get_task_config("峰哥")
    if not resolved:
        raise RuntimeError("未能从测试配置副本解析峰哥三层继承配置")
    resolved_path = destination / "resolved_DMR-峰哥.yml"
    resolved_path.write_text(
        yaml.safe_dump(resolved, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return resolved_path


def update_manifest(section: str) -> None:
    """Replace the generated full-coverage section without duplicating it."""
    manifest = ROOT / "perf" / "samples.md"
    existing = manifest.read_text(encoding="utf-8") if manifest.exists() else ""
    marker = "\n## full_coverage/峰哥 8月18日14点19分"
    prefix = existing.split(marker, 1)[0].rstrip()
    manifest.write_text(prefix + "\n\n" + section.rstrip() + "\n", encoding="utf-8")


def main() -> None:
    """Create the full_coverage sample, config copy, and manifest entry."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=pathlib.Path, default=ROOT / "perf" / "samples" / "full_coverage")
    parser.add_argument("--config-output", type=pathlib.Path, default=ROOT / "perf" / "samples" / "full_coverage" / "配置")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    metadata, events = read_events(SOURCE_JSONL)
    source_start = float(metadata["segment_start_time"])
    selected = [event for event in events if START_SECONDS <= event["received_at"] - source_start < START_SECONDS + DURATION_SECONDS]
    selected_start = source_start + START_SECONDS
    synthetic = (
        synthetic_gifts(selected_start)
        + synthetic_superchats(selected_start)
        + synthetic_members(selected_start)
    )
    all_events = selected + synthetic
    all_events.sort(key=lambda event: event["received_at"])
    output_video = args.output / "原始" / "峰哥_8月18日14点19分_1860-2160.flv"
    output_jsonl = args.output / "补充" / "峰哥_8月18日14点19分_1860-2160_augmented.jsonl"
    output_video.parent.mkdir(parents=True, exist_ok=True)
    output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(START_SECONDS), "-i", str(SOURCE_VIDEO), "-t", str(DURATION_SECONDS), "-c", "copy", str(output_video)], check=True)
    with output_jsonl.open("w", encoding="utf-8") as target:
        target.write(json.dumps({**metadata, "segment_start_time": selected_start}, ensure_ascii=False) + "\n")
        for event in all_events:
            target.write(json.dumps(event, ensure_ascii=False) + "\n")
    vip_uid = Counter(event["uid"] for event in selected if event.get("type") == "danmaku").most_common(1)[0][0]
    config_path = write_config_copy(args.config_output, vip_uid)
    resolved_config_path = write_resolved_config(config_path, args.config_output)
    counts = Counter(event.get("type") for event in all_events)
    counts["emoji_or_emoticon"] = sum(bool(event.get("emoticon")) or any(char in event.get("content", "") for char in "😀😂🤣😅🥹") or "[" in event.get("content", "") for event in all_events)
    update_manifest(
        f"""## full_coverage/峰哥 8月18日14点19分
- 原始视频：{SOURCE_VIDEO}
- 原始弹幕：{SOURCE_JSONL}
- 截取：{START_SECONDS}s–{START_SECONDS + DURATION_SECONDS}s；输出：{output_video}
- 增强弹幕：{output_jsonl}；真实事件数量：{len(selected)}；总事件数量：{len(all_events)}；类型：{dict(counts)}
- 真实 SC 价格（片段相对秒）："""
        + ", ".join(
            f"{event['price_cny']} 元（{event['received_at'] - selected_start:.1f}s）"
            for event in selected
            if event.get("type") == "superchat"
        )
        + f"\n- 测试配置副本：{config_path}；三层继承快照：{resolved_config_path}；VIP 用户：{vip_uid}（原配置无 vip_users，未修改正式配置）\n"
        "- 合成事件：6 个 handwritten SC，价格 0.5/5/20/75/300/1200 元，在 30–35 秒内密集进入；0.5 元至 30 秒档先退出，验证 slide_left reflow、重叠进出和六档颜色/时长。\n"
        "- 合成事件：6 个 handwritten 礼物，价格 0.5/5/20/75/300/1200 元，集中在片段 20–25 秒；0.5 元礼物低于 slide_right.gift.min_price_cny=1，预期不显示。\n"
        "- 合成事件：2 个 modified 会员，22s 和 24s，与礼物同段进入，验证 slide_right 共用队列。\n"
    )
    print(json.dumps({"video": str(output_video), "jsonl": str(output_jsonl), "config": str(config_path), "resolved_config": str(resolved_config_path), "vip_uid": vip_uid, "counts": counts}, ensure_ascii=False))


if __name__ == "__main__":
    main()
