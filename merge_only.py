"""手动合并：把同一场直播的多个 MP4 按顺序无损拼接成一个文件（原文件保留）。

用法：python merge_only.py
依次询问：视频（文件夹或逐个文件）→ 合并顺序 → 音量 → 输出位置 → 确认。
"""

from datetime import datetime
from pathlib import Path
import math
import os
import shutil
import tempfile

from DMR.Merger.video_merge import (
    amplify_mp4,
    concat_copy,
    group_by_resolution,
    probe_seg,
    retime,
)
from DMR.utils.prompts import (
    ask_choice,
    ask_numbers,
    ask_output,
    ask_output_folder,
    ask_path,
    confirm,
    input_path,
    protect,
)


def is_mp4(path):
    return os.path.isfile(path) and path.lower().endswith(".mp4")


def format_duration(seconds):
    seconds = int(seconds)
    return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def merge_mp4(
    metas,
    target_path,
    amplify_audio=False,
    extra_gain_db=0,
) -> Path:
    """按当前 DMR Merger 的规则无损合并一组同分辨率 MP4（metas 来自 probe_seg）。"""
    groups = group_by_resolution(metas)
    if len(groups) != 1:
        resolutions = [group[0].get("wh") for group in groups]
        raise RuntimeError(
            "输入视频存在不同分辨率或探测失败，不能安全合并成一个文件："
            f"{resolutions}"
        )

    target = Path(target_path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    workdir = Path(
        tempfile.mkdtemp(prefix=".merge_only_tmp_", dir=str(target.parent))
    )
    try:
        group = groups[0]
        product = workdir / "merged.mp4"
        if len(group) == 1:
            shutil.copy2(group[0]["path"], product)
        else:
            reference = max(group, key=lambda item: item.get("dur") or 0)
            target_timescale = reference.get("ts")
            inputs = []
            for index, segment in enumerate(group):
                if (
                    target_timescale
                    and segment.get("ts")
                    and segment["ts"] != target_timescale
                ):
                    adjusted = workdir / f"retime_{index:03d}.mp4"
                    retime(segment["path"], adjusted, target_timescale)
                    inputs.append(adjusted)
                else:
                    inputs.append(segment["path"])
            concat_copy(inputs, str(product))

        if amplify_audio:
            product = Path(
                amplify_mp4(
                    product,
                    target_db=-1,
                    extra_gain_db=extra_gain_db,
                    remover=False,
                )
            )

        product.replace(target)
        return target
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def ask_videos():
    path = ask_path(
        "请输入要合并的视频文件夹，或第一个 MP4",
        lambda p: os.path.isdir(p) or is_mp4(p),
        "路径不存在或不是 MP4/文件夹，请重新输入。",
    )
    if is_mp4(path):
        videos = [path]
        while True:
            value = input_path(f"第 {len(videos) + 1} 个 MP4（可拖入，回车结束）：")
            if not value:
                return videos
            if is_mp4(value):
                videos.append(os.path.abspath(value))
            else:
                print("文件不存在或不是 MP4，请重新输入。")

    videos = sorted(
        (str(p) for p in Path(path).iterdir() if is_mp4(str(p))),
        key=os.path.getctime,
    )
    if not videos:
        print("该文件夹中没有 MP4。")
        return []
    print("该文件夹中的 MP4（按创建时间排序）：")
    for index, video in enumerate(videos, 1):
        created = datetime.fromtimestamp(os.path.getctime(video))
        print(f"  [{index}] {os.path.basename(video)}    创建于 {created:%m-%d %H:%M}")
    picked = ask_numbers(
        "按合并顺序输入编号（空格分隔，回车按上面顺序全选，n取消）：",
        len(videos),
        range(len(videos)),
    )
    return [videos[index] for index in picked]


def ask_gain_db():
    while True:
        value = input("额外增益 dB（回车：0，只把峰值提到 -1 dBFS）：").strip()
        if not value:
            return 0.0
        try:
            gain_db = float(value)
        except ValueError:
            gain_db = -1
        if math.isfinite(gain_db) and gain_db >= 0:
            return gain_db
        print("请输入大于或等于 0 的数字，例如 5、10 或 20。")


def main():
    videos = ask_videos()
    if len(videos) < 2:
        print("至少需要两个视频，没有开始合并。")
        return
    metas = [probe_seg(video) for video in videos]

    amplify_audio = (
        ask_choice("音量：", ["保持原音量", "放大（峰值提到 -1 dBFS）"]) == 1
    )
    extra_gain_db = ask_gain_db() if amplify_audio else 0.0

    first = Path(videos[0])
    directory = ask_output_folder(str(first.parent))
    target, overwrite = ask_output(
        directory, f"{first.stem}_merged.mp4", True, protect(*videos)
    )
    if not target:
        print("没有开始合并。")
        return

    lines = ["合并顺序 | 分辨率 | 时长"]
    for index, meta in enumerate(metas, 1):
        size = "x".join(map(str, meta["wh"])) if meta["wh"] else "探测失败"
        lines.append(
            f"{index}. {meta['path'].name} | {size} | {format_duration(meta['dur'])}"
        )
    lines.append(
        "音量："
        + (f"放大，额外增益 {extra_gain_db:g} dB" if amplify_audio else "保持原音量")
    )
    lines.append(f"输出：{target}" + (" [覆盖]" if overwrite else ""))
    if not confirm(lines):
        print("没有开始合并。")
        return

    print("正在无损合并（原文件会保留）……")
    final_path = merge_mp4(
        metas,
        target,
        amplify_audio=amplify_audio,
        extra_gain_db=extra_gain_db,
    )
    print(f"合并完成：{final_path}")


if __name__ == "__main__":
    main()
