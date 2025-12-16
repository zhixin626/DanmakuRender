import subprocess
from pathlib import Path
from DMR.utils.utils import safe_filename
def parse_time_input(s: str) -> int:
    """
    支持两种输入：
    1) "3600"             -> 3600 秒
    2) "1 39 0" / "39 0"  -> 时 分 秒 / 分 秒
    返回字符串形式的总秒数，直接给 ffmpeg 用。
    """
    s = s.strip()
    parts = s.split(" ")
    if len(parts) == 3:
        h, m, sec = map(int, parts)
    elif len(parts) == 2:
        h = 0
        m, sec = map(int, parts)
    elif len(parts) == 1:
        h = 0
        m = 0
        sec = int(parts[0])
    else:
        raise ValueError(f"无法解析时间：{s!r}")

    if m < 0 or sec < 0 or h < 0:
        raise ValueError("时间不能为负数")

    total_sec = h * 3600 + m * 60 + sec
    return total_sec


def main():
    # ---------- 输入部分 ----------
    src = input("请输入需要截取的视频路径：\n").strip()

    if not src:
        src="D:/DanmakuRender/Tasks/佐佐酱（弹幕版）/佐11月30日（弹幕）.mp4"
        print(f"输入路径为空，[debug]将使用默认路径{src}")


    if src.startswith('"') and src.endswith('"'):
        src = src[1:-1]
    if src.startswith("'") and src.endswith("'"):
        src = src[1:-1]
    src_path = Path(src)
    if not src_path.exists():
        print(f"文件不存在：{src_path}")
        return

    start_raw = input("请输入起点（可以输入总秒数，如 3600；或 '时 分 秒'，如 '1 0 0'）：\n")
    end_raw = input("请输入终点（可以输入总秒数，如 3600；或 '时 分 秒'，如 '1 0 0'）：\n")

    try:
        start = parse_time_input(start_raw)
        end = parse_time_input(end_raw)
    except ValueError as e:
        print("时间格式错误：", e)
        return
    # ---------- 输出路径 ----------
    out_path = safe_filename(src_path.with_stem(src_path.stem + "_clipped"))

    src_str = str(src_path)
    out_str = str(out_path)

    print(f"\n输出文件将保存到：{out_str}\n")

    # ---------- ffmpeg 命令 ----------
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel","error",
        "-stats",
        "-y",               # 覆盖输出
        "-ss", str(start),       # 起点
        "-i", src_str,
        "-t", str(end-start),         # 终点
        "-c", "copy",       # 尽量不重新编码，更快
        out_str
    ]

    print("正在执行 ffmpeg…")
    print(f"cmd命令为{cmd}\n")

    # ---------- 调用 ffmpeg ----------
    try:
        subprocess.run(cmd, check=True)
        print("\n截取成功！")
        print(f"输出文件：{out_str}")
    except subprocess.CalledProcessError:
        print("\nffmpeg 执行失败，请检查起点/终点格式是否正确。")

if __name__ == "__main__":
    main()
