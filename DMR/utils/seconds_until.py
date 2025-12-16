from datetime import datetime, timedelta
def parse_time_str(time_str:str):
    parts = list(map(int, time_str.split(":")))
    if len(parts) == 2:
        hour, minute = parts
        second = 0
    elif len(parts) == 3:
        hour, minute, second = parts
    else:
        raise ValueError(f"非法时间格式: {time_str}")
    if not (0 <= hour <= 23):
        raise ValueError("hour 必须在 0~23")
    if not (0 <= minute <= 59):
        raise ValueError("minute 必须在 0~59")
    if not (0 <= second <= 59):
        raise ValueError("second 必须在 0~59")
    return hour,minute,second

def seconds_until(time_str: str):
    """
    time_str: 'HH:MM' or 'HH:MM:SS'
    """
    now = datetime.now()

    hour,minute,second=parse_time_str(time_str)
    target = now.replace(
        hour=hour,
        minute=minute,
        second=second,
        microsecond=0
    )
    if now >= target:
        target += timedelta(days=1)

    return max(0,(target - now).total_seconds())

def time_to_minutes(t: str) -> int:
    h, m = map(int, t.split(":"))
    return h * 60 + m


def is_now_in_time_ranges(ranges=None, now=None):
    if not ranges:
        return False
    now = now or datetime.now()
    now_min = now.hour * 60 + now.minute

    for r in ranges:
        start = time_to_minutes(r["start"])
        end = time_to_minutes(r["end"])

        if start <= end:
            # 非跨天，如 12:00 - 13:30
            if start <= now_min < end:
                return True
        else:
            # 跨天，如 23:30 - 01:00
            if now_min >= start or now_min < end:
                return True

    return False

def get_start_check_interval(advanced_video_args, now=None) -> int:
    now = now or datetime.now()

    default_interval = int(advanced_video_args.get("start_check_interval", 60))

    policy = advanced_video_args.get("start_check_policy", {}) or {}
    if not policy.get("enabled", False):
        return default_interval

    ranges = policy.get("ranges", [])
    if not ranges:
        return default_interval

    for r in ranges:
        if is_now_in_time_ranges([{"start": r["start"], "end": r["end"]}], now=now):
            return int(r.get("interval", default_interval))

    return default_interval

if __name__ == '__main__':
    import yaml
    file=R"D:\DanmakuRender\configs\DMR-不可一世杀手.yml"
    with open(file,'r',encoding="UTF-8") as f:
        config=yaml.safe_load(f)
    adv_args=config.get("download_args").get("advanced_video_args")
    ranges=adv_args.get("start_check_policy").get("ranges")
    print(get_start_check_interval(adv_args))

