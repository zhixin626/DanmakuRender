from pathlib import Path
from DMR.utils.utils import safe_filename
from DMR.utils.merge_mp4 import merge_mp4,amplify_mp4
from upload_only import file_to_args, replace_args_keywords,parse_yn,strip_quotes

def main():
    print("请输入需要按顺序合并的视频路径,每行一个,输入空行结束：")
    videos = []
    while True:
        inp = strip_quotes(input("> ").strip())
        if not inp:
            break
        if not Path(inp).exists():
            print("❌ 文件不存在，请重新输入。")
            continue
        videos.append(inp)

    if not videos:
        print("❌ 未输入任何视频文件，程序退出。")
        return

    # 询问是否 amplify
    is_amplify = parse_yn("合并完成后是否要按照config文件增强音频？(y/n),默认n\n", default=False)

    # 询问是否 remover（删除源文件）
    remover= parse_yn("是否把原始视频移入回收站？(y/N),默认N\n", default=False)

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

    print("正在合并......将原始文件移入垃圾桶" if remover else "正在合并......不会移动原始文件")

    # 合并动作：merge_mp4 永远输出到 merged.mp4
    tmp_output = merge_mp4(videos, remover=remover)  # 返回的是 Path / str
    tmp_output = Path(tmp_output)

    # 根据用户想要的最终名称进行 safe_filename
    final_path_merged = safe_filename(target_path)

    if final_path_merged != target_path:
        print(f"⚠ 警告：目标文件名 \n{target_path}\n 已存在，将自动改为：\n{final_path_merged}")
    else:
        print(f"☑ 将命名为：{final_path_merged}")

    # 重命名
    Path(tmp_output).rename(final_path_merged)
    print(f"🎉 合并完成：{final_path_merged}")

    if is_amplify:
        common_event_args, _, _ = replace_args_keywords(*file_to_args(videos[0]))
        merge_args=common_event_args.get("merge_args",{})
        extra_gain_db=merge_args.get("extra_gain_db",0)
        final_path_amplified=amplify_mp4(final_path_merged,extra_gain_db=extra_gain_db, remover=remover)
        print(f"🎉 增强完成：{final_path_amplified}")


if __name__ == "__main__":
    main()
