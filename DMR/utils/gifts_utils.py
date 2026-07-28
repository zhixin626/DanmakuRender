import json
import os
from collections import Counter
from datetime import datetime


REVENUE_EVENT_TYPES = {"gift", "superchat", "member"}


def _number(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def generate_gift_statistics(
    jsonl_paths,
    stat_path,
    start_time=None,
    rank_top=3,
    event_types=None,
):
    """从当前直播的分段事件 JSONL 汇总礼物、SC 和会员数据。

    输入文件仍属于对应视频分段，不会在统计后删除；统计结果是独立的派生文件。
    """
    selected_types = {
        str(value).lower() for value in (event_types or REVENUE_EVENT_TYPES)
    } & REVENUE_EVENT_TYPES
    entries = []

    for path in dict.fromkeys(jsonl_paths or []):
        if not path or not os.path.isfile(path):
            continue
        with open(path, "r", encoding="utf-8") as fp:
            lines = fp.readlines()
            last_content_index = max(
                (index for index, line in enumerate(lines) if line.strip()),
                default=-1,
            )
            for index, raw_line in enumerate(lines):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    # 进程被强杀时，只忽略文件末尾可能残留的半行。
                    if index == last_content_index:
                        break
                    raise
                if data.get("type") in selected_types:
                    entries.append(data)

    if not entries:
        return None

    gift_entries = [item for item in entries if item["type"] == "gift"]
    sc_entries = [item for item in entries if item["type"] == "superchat"]
    member_entries = [item for item in entries if item["type"] == "member"]

    gifts_revenue = sum(_number(item.get("price_cny")) for item in gift_entries)
    sc_revenue = sum(_number(item.get("price_cny")) for item in sc_entries)
    member_revenue = sum(_number(item.get("price_cny")) for item in member_entries)
    total_revenue = gifts_revenue + sc_revenue + member_revenue
    num_of_sc = len(sc_entries)
    num_of_gifters = len({item.get("uname", "") for item in gift_entries})

    member_counts = Counter()
    for item in member_entries:
        name = item.get("member_name") or "未知"
        member_counts[name] += _number(item.get("member_count")) or 1
    member_info = (
        ",".join(f"{count:g}个{name}" for name, count in member_counts.items())
        if member_counts
        else "无"
    )

    revenue_parts = []
    if gift_entries:
        revenue_parts.append(f"{round(gifts_revenue, 2):g}元({num_of_gifters}人)")
    if sc_entries:
        revenue_parts.append(f"{round(sc_revenue, 2):g}元({num_of_sc}条SC)")
    if member_entries:
        revenue_parts.append(f"{round(member_revenue, 2):g}元({member_info})")
    revenue_summary = "+".join(revenue_parts)
    if len(revenue_parts) > 1:
        revenue_summary += f"={round(total_revenue, 2):g}元"

    user_totals = Counter()
    for item in entries:
        user_totals[item.get("uname") or "未知"] += _number(item.get("price_cny"))
    top_ranking = [
        {"name": name, "total_value": round(price, 2)}
        for name, price in user_totals.most_common(int(rank_top))
    ]

    report_time = start_time or datetime.now()
    stat_entry = {
        "timestamp": report_time.strftime("%Y-%m-%d %H:%M:%S"),
        "gifts_revenue": round(gifts_revenue, 2),
        "num_of_gifters": num_of_gifters,
        "sc_revenue": round(sc_revenue, 2),
        "num_of_sc": num_of_sc,
        "member_revenue": round(member_revenue, 2),
        "member_info": member_info,
        "total_revenue": round(total_revenue, 2),
        "revenue_summary": revenue_summary,
        "top_ranking": top_ranking,
    }

    os.makedirs(os.path.dirname(stat_path) or ".", exist_ok=True)
    with open(stat_path, "a", encoding="utf-8") as fp:
        fp.write(json.dumps(stat_entry, ensure_ascii=False) + "\n")
    return stat_entry
