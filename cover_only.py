"""手动做封面：从视频抽帧，或按任务的封面配置渲染模板 / 文字封面。

用法：python cover_only.py
依次询问：视频 → 任务配置 → 封面类型 → 文字等参数 → 输出位置 → 确认。
默认值来自所选任务的 upload_args.*.cover（default → global → task）。
"""

from datetime import datetime
import logging
import os
import shutil
import sys

from DMR.Config import Config
from DMR.utils import merge_dict, replace_keywords
from DMR.utils.cover import prepare_cover
from DMR.utils.prompts import (
    ask_choice,
    ask_output,
    ask_output_folder,
    ask_path,
    ask_task,
    confirm,
    input_path,
    protect,
    read_task_file,
)

MODES = {
    "extract_frame": "从视频抽一帧",
    "render_template": "模板封面（主播名 + 日期）",
    "render_text": "纯文字封面",
    "render_on_frame": "模板封面叠在视频画面上",
    "render_text_on_frame": "文字叠在视频画面上",
}
TEXT_MODES = {"render_text", "render_text_on_frame"}
ON_FRAME_MODES = {"render_on_frame", "render_text_on_frame"}
VIDEO_EXTENSIONS = (".flv", ".ts", ".mp4")
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")


def is_file_with(path, extensions):
    return os.path.isfile(path) and path.lower().endswith(extensions)


def ask_optional_video():
    while True:
        path = input_path("视频文件（可拖入，纯文字/模板封面可直接回车）：")
        if not path:
            return None
        if is_file_with(path, VIDEO_EXTENSIONS):
            return os.path.abspath(path)
        print("文件不存在或不是 FLV/TS/MP4，请重新输入。")


def ask_video():
    return ask_path(
        "这种封面需要视频，请输入视频文件",
        lambda path: is_file_with(path, VIDEO_EXTENSIONS),
        "文件不存在或不是 FLV/TS/MP4，请重新输入。",
    )


def ask_text(label, default):
    return input(f"{label}（回车：{default}）：").strip() or default


def cover_config(config, task_file):
    """The cover section of each upload account of the task, merged over
    the upload target's global defaults; asks when accounts differ."""
    targets = config.global_config["upload_args"]
    uploads = []
    if task_file:
        for value in (read_task_file(task_file).get("upload_args") or {}).values():
            uploads += value if isinstance(value, list) else [value]
    covers = []
    for upload in uploads or [{}]:
        target = targets.get(upload.get("target", "bilibili")) or {}
        cover = merge_dict(target.get("cover") or {}, upload.get("cover") or {})
        if all(cover != known for _, known in covers):
            covers.append((upload.get("account", "全局配置"), cover))
    if len(covers) == 1:
        return covers[0][1]
    index = ask_choice("封面参数来自账号：", [account for account, _ in covers])
    return covers[index][1]


def ask_frame(video):
    """Background picture for the on-frame modes; None means grab from video."""
    while True:
        hint = "回车：从视频抽帧" if video else "必填"
        path = input_path(f"背景图片（可拖入，{hint}）：")
        if not path and video:
            return None
        if is_file_with(path, IMAGE_EXTENSIONS):
            return os.path.abspath(path)
        print("图片不存在或不是 PNG/JPG/WEBP，请重新输入。")


def main():
    logging.basicConfig(level=logging.WARNING, format="[%(levelname)s] %(message)s")
    config = Config("configs/global.yml")

    video = ask_optional_video()
    folder = os.path.dirname(video) if video else os.getcwd()
    taskname, task_file = ask_task(config, folder)
    cover = cover_config(config, task_file)

    modes = list(MODES)
    mode = modes[
        ask_choice(
            "封面类型：",
            list(MODES.values()),
            modes.index(cover["mode"]) if cover.get("mode") in MODES else 0,
        )
    ]
    if mode in ON_FRAME_MODES:
        frame = ask_frame(video)
        if frame is None and not video:
            video = ask_video()
    else:
        frame = None
        if mode == "extract_frame" and not video:
            video = ask_video()

    stime = (
        datetime.fromtimestamp(min(os.path.getctime(video), os.path.getmtime(video)))
        if video
        else datetime.now()
    )
    context = {"streamer": {"name": taskname or ""}, "stime": stime, "now": stime}
    cover = dict(cover, mode=mode, path="", frame_path=frame)
    if mode in TEXT_MODES:
        texts = [replace_keywords(str(text), context) for text in cover.get("texts") or []]
        answer = ask_text("文字（多行用 / 分隔）", " / ".join(texts))
        cover["texts"] = [line.strip() for line in answer.split("/") if line.strip()]
        colors = cover.get("colors")
        if isinstance(colors, list) and len(colors) not in (1, len(cover["texts"])):
            cover["colors"] = colors[:1]
        summary = "文字：" + " / ".join(cover["texts"])
    elif mode != "extract_frame":
        cover["name"] = ask_text(
            "主播名", replace_keywords(str(cover.get("name", "")), context)
        )
        cover["time_template"] = ask_text(
            "日期文字",
            replace_keywords(
                str(cover.get("time_template", "{stime.month}月{stime.day}日")),
                context,
            ),
        )
        summary = f"主播名：{cover['name']}  日期：{cover['time_template']}"
    else:
        summary = f"裁剪比例：{cover.get('ratio', '16/9')}"

    stem = os.path.splitext(os.path.basename(video))[0] + "_" if video else ""
    extension = ".jpg" if mode == "extract_frame" else ".png"
    directory = ask_output_folder(os.path.dirname(video) if video else os.getcwd())
    output, overwrite = ask_output(
        directory, f"{stem}封面{extension}", True, protect(video, frame)
    )
    if not output:
        print("没有开始生成。")
        return 0

    lines = [f"封面类型：{MODES[mode]}", summary]
    if video:
        lines.append(f"视频：{video}")
    if frame:
        lines.append(f"背景图片：{frame}")
    lines.append(f"输出：{output}" + (" [覆盖]" if overwrite else ""))
    if not confirm(lines):
        print("没有开始生成。")
        return 0

    # prepare_cover 把成品写在 video_path 所在目录；不需要视频时用输出目录里的
    # 占位路径，它不会被读取。
    source = video or os.path.join(directory, "_cover_source.mp4")
    result, generated = prepare_cover(source, cover, stime=stime, account="manual")
    if not (result and generated):
        print("封面生成失败，原因见上方日志。")
        return 1
    shutil.copyfile(result, output)
    os.remove(result)
    print(f"封面已保存到 {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
