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
from DMR.utils.utils import safe_filename


def strip_quotes(value: str) -> str:
    value = value.strip()
    if (
        len(value) >= 2
        and value[0] == value[-1]
        and value[0] in {'"', "'"}
    ):
        return value[1:-1]
    return value


def merge_mp4(
    videos,
    target_path,
    amplify_audio=False,
    extra_gain_db=0,
) -> Path:
    """按当前 DMR Merger 的规则无损合并一组同分辨率 MP4。"""
    paths = [Path(video).resolve() for video in videos]
    metas = [probe_seg(path) for path in paths]
    groups = group_by_resolution(metas)
    if len(groups) != 1:
        resolutions = [group[0].get("wh") for group in groups]
        raise RuntimeError(
            "输入视频存在不同分辨率或探测失败，不能安全合并成一个文件："
            f"{resolutions}"
        )

    target = Path(safe_filename(Path(target_path).resolve()))
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


def parse_yn(prompt: str, default: bool) -> bool:
    s = input(prompt).strip().lower()
    if s == "":
        print(f"无输入，按默认值 {default} 处理。")
        return default
    if s in ("y", "yes", "1", "true", "t"):
        return True
    if s in ("n", "no", "0", "false", "f"):
        return False
    print(f"输入无效，按默认值 {default} 处理。")
    return default


def parse_gain_db(prompt: str, default: float = 0.0) -> float:
    while True:
        value = input(prompt).strip()
        if not value:
            return float(default)
        try:
            gain_db = float(value)
        except ValueError:
            print("请输入大于或等于 0 的数字，例如 5、10 或 20。")
            continue
        if not math.isfinite(gain_db) or gain_db < 0:
            print("请输入大于或等于 0 的有限数字。")
            continue
        return gain_db


def main():
    print("请输入需要按顺序合并的视频【文件或文件夹】路径,每行一个,输入空行结束：")
    videos = []
    while True:
        inp = strip_quotes(input("> ").strip())
        if not inp:
            break

        p = Path(inp)
        if not p.exists():
            print("❌ 路径不存在，请重新输入。")
            continue

        # 输入的是文件：沿用原逻辑（但顺手限定一下 mp4，更安全）
        if p.is_file():
            if p.suffix.lower() != ".mp4":
                print("❌ 不是 mp4 文件，请重新输入。")
                continue
            videos.append(str(p))
            continue

        if p.is_dir():
            mp4s = [x for x in p.iterdir() if x.is_file() and x.suffix.lower() == ".mp4"]
            if not mp4s:
                print("❌ 文件夹下没有 mp4。")
                continue

            mp4s.sort(key=lambda x: os.path.getctime(x))

            print("📂 检测到文件夹，按创建时间排序后的 mp4 如下：")
            for i, f in enumerate(mp4s, 1):
                print(f"  {i:02d}. {f.name}")

            ok = parse_yn("以上顺序是否正确？(y/n)，默认 y\n", default=True)
            if not ok:
                print("🔁 顺序未确认，请重新输入路径。")
                continue

            videos.extend(str(x) for x in mp4s)
            print(f"✅ 已确认并加入 {len(mp4s)} 个 mp4：{p}")
            break

    if not videos:
        print("❌ 未输入任何视频文件，程序退出。")
        return

    # 询问输出名称
    print("请输入输出文件名或输出路径(可空行,默认为 *_merged.mp4):")
    target_path = strip_quotes(input("> ").strip())

    # 默认输出：第一个文件名 + "_merged.mp4"
    if not target_path:
        first = Path(videos[0])
        fp = first.with_name(first.stem + "_merged" + first.suffix)
    else:
        fp = Path(target_path)
        if fp.parent == Path("."):
            first = Path(videos[0])
            fp = first.parent / fp.name
    if fp.suffix == "":
        fp = fp.with_suffix(".mp4")
    target_path = str(fp)

    amplify_audio = parse_yn(
        "是否放大音频？(y/n)，默认 n\n",
        default=False,
    )
    extra_gain_db = 0.0
    if amplify_audio:
        extra_gain_db = parse_gain_db(
            "请输入额外增益 dB；0 表示仅自动提升峰值到 -1 dBFS，默认 0\n> ",
            default=0,
        )
        print(
            "🔊 合并后将检测音频峰值并放大："
            f"峰值目标 -1 dBFS，额外增益 {extra_gain_db:g} dB。"
        )
    print("正在按当前 DMR Merger 规则无损合并（原始文件会保留）")
    final_path = merge_mp4(
        videos,
        target_path,
        amplify_audio=amplify_audio,
        extra_gain_db=extra_gain_db,
    )
    print(f"🎉 合并完成：{final_path}")

if __name__ == "__main__":
    main()
