from pathlib import Path
from DMR.utils.utils import safe_filename
from DMR.utils.merge_mp4 import merge_mp4,amplify_mp4
from upload_only import file_to_args,strip_quotes
import os
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

    print("正在合并(将原始文件移入垃圾桶)")

    # 合并动作：merge_mp4 永远输出到 merged.mp4
    tmp_output = merge_mp4(videos)  # 返回的是 Path / str
    tmp_output = Path(tmp_output)

    common_event_args, _, _ = file_to_args(videos[0])
    merge_args=common_event_args.get("merge_args",{})
    extra_gain_db=merge_args.get("extra_gain_db",0)
    # is_amplify=merge_args.get("is_amplify",False)
    # if is_amplify:
    #     print(f"正在按配置文件增强音频{extra_gain_db}db")
    #     tmp_output=amplify_mp4(tmp_output,extra_gain_db=extra_gain_db)

    final_path=rename_if_needed(tmp_output,Path(target_path))
    print(f"🎉 合并完成：{final_path}")


def rename_if_needed(src: Path, target: Path):
    if src == target:
        return src
    safe_target = safe_filename(target)
    print(f"⚠ 警告：目标文件名 \n{target}\n 已存在，将自动改为：\n{safe_target}")
    src.rename(safe_target)
    return safe_target

if __name__ == "__main__":
    main()
