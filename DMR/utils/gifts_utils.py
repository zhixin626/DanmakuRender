import json
import os
from collections import Counter
from datetime import datetime
from send2trash import send2trash
def save_gift_to_jsonl(dm, file_path: str):
    """
    将礼物弹幕信息保存至指定的 JSONL 文件。
    :param dm: 传入的礼物弹幕对象 (GiftDanmaku)
    :param file_path: 目标文件路径 (如 "./test文件/gifts.jsonl")
    """
    # 1. 确保目标文件夹存在 (Ensure directory exists)
    directory = os.path.dirname(file_path)
    if directory:
        os.makedirs(directory, exist_ok=True)

    gift_data = {
        "uname": getattr(dm, 'uname', '未知用户'),
        "gift_name": getattr(dm, 'gift_name', '未知礼物'),
        "count": getattr(dm, 'gift_count', 1),
        "total_price_cny": getattr(dm, 'total_price_cny', 0.0)
    }

    # 3. 写入文件 (Append mode)
    # open会创建file_path如果不存在
    with open(file_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(gift_data, ensure_ascii=False) + "\n")

def generate_gift_statistics(jsonl_paths, stat_path, start_time=None, rank_top=3, delete_after_process=True):
    """
    :param start_time: 传入 datetime 对象，作为统计结果的时间戳。如果为 None 则用当前时间。
    """
    all_gifts = []

    # 1. 简单的读取逻辑 (Simple Read)
    for path in jsonl_paths:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        all_gifts.append(json.loads(line))

    if not all_gifts:
        return None

    # 2. 核心计算 (Core Calculation)
    total_revenue = sum((g.get("total_price_cny") or 0) for g in all_gifts)

    # 计算每个人的总额
    user_totals = Counter()
    for g in all_gifts:
        user_totals[g["uname"]] += (g.get("total_price_cny") or 0)

    # 获取前 N 名
    top_ranking = [
        {"name": name, "total_value": round(price, 2)}
        for name, price in user_totals.most_common(rank_top)
    ]

    # 3. 构建结果字典 (Result Dictionary)
    # 使用传入的 start_time，如果没传就用现在的时间
    report_time = start_time if start_time else datetime.now()

    stat_entry = {
        "timestamp": report_time.strftime("%Y-%m-%d %H:%M:%S"),
        "total_revenue": round(total_revenue, 2),
        "total_gifters": len(user_totals),
        "top_ranking": top_ranking
    }

    # 4. 写入文件 (Write to file)
    os.makedirs(os.path.dirname(stat_path), exist_ok=True)
    with open(stat_path, "a", encoding="utf-8") as f:
        f.write("\n" + json.dumps(stat_entry, ensure_ascii=False))

    # 5. 清理文件 (Cleanup)
    if delete_after_process:
        for path in jsonl_paths:
            if os.path.exists(path):
                send2trash(path)

    # 返回这个字典，方便你在外部直接使用数据 (Return for external use)
    return stat_entry
