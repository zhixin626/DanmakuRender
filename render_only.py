"""渲染专用脚本（离线批量）：对文件夹里的视频和同名 JSONL/ASS 渲染或转码。

引擎选择与主管线一致（见 liveevents._check_for_render）：
  - render_args.overlay.engine 选择 dmrender / emoji_dmrender / gpu_render；
  - JSONL 由 LayoutCompiler 依次生成每段 RenderPlan，并维护跨段 track；
  - 同名 ASS 仍作为旧文件兼容输入；
  - --emoji / --no-emoji 可强制覆盖配置。
  - --transcode：弹幕引擎能不烧弹幕重编码时（gpu_render）沿用弹幕版的编码设置，
    输出与弹幕版同格式、可无损合并；否则使用 render_args.transcode。

用法：
    python render_only.py [--config configs/global.yml] [--input_dir 路径]
                          [--output_dir 路径] [--transcode] [--emoji | --no-emoji]
"""

import argparse
import json
import os
import sys
import logging
import tempfile
from glob import glob
from os.path import exists, join

sys.path.append('..')
sys.path.append('.')

from DMR.Config import Config
from DMR.Render import get_engine, DEFAULT_ENGINE, EMOJI_ENGINE, TRANSCODE_ENGINE
from DMR.utils import FFprobe, VideoInfo
from DMR.utils.prompts import (
    OUTPUT_SUFFIXES,
    ask_numbers,
    ask_output,
    ask_output_folder,
    ask_path,
    ask_task as ask_task_name,
    confirm as confirm_lines,
    input_path,
    protect,
)


def show_progress(label, pct):
    """Erase the previous line before writing a shorter progress value."""
    print(f"\r\x1b[2K{label} 进度 {float(pct):.1f}%", end="", flush=True)


def enable_console_escapes():
    """Enable erase-line escapes in native Windows console sessions."""
    if os.name != "nt" or not sys.stdout.isatty():
        return
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.windll.kernel32
    kernel.GetStdHandle.restype = wintypes.HANDLE
    kernel.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    handle = kernel.GetStdHandle(-11)
    mode = wintypes.DWORD()
    if kernel.GetConsoleMode(handle, ctypes.byref(mode)):
        kernel.SetConsoleMode(handle, mode.value | 0x0004)


def danmaku_candidates(video: str) -> list[str]:
    base = os.path.splitext(video)[0]
    jsonl = [
        path
        for path in (base + ".jsonl", *glob(base + "_*.jsonl"))
        if os.path.isfile(path)
    ]
    jsonl = sorted(
        set(jsonl),
        key=lambda path: (
            (
                0
                if path.lower() == (base + "_augmented.jsonl").lower()
                else 1 if path.lower() == (base + ".jsonl").lower() else 2
            ),
            path.lower(),
        ),
    )
    return jsonl + ([base + ".ass"] if os.path.isfile(base + ".ass") else [])


def source_videos(folder: str) -> list[str]:
    files = [path for path in glob(join(folder, "*")) if os.path.isfile(path)]
    flv_stems = {
        os.path.splitext(os.path.basename(path))[0].casefold()
        for path in files
        if path.lower().endswith(".flv")
    }
    return sorted(
        path
        for path in files
        if os.path.splitext(path)[1].lower() in (".flv", ".ts", ".mp4")
        and not any(
            marker in os.path.basename(path) for marker in OUTPUT_SUFFIXES
        )
        and not (
            path.lower().endswith(".mp4")
            and os.path.splitext(os.path.basename(path))[0].casefold() in flv_stems
        )
    )


def ask_video(default: str = "") -> str:
    return ask_path(
        "请输入视频文件或文件夹",
        lambda path: os.path.isdir(path)
        or (os.path.isfile(path) and path.lower().endswith((".flv", ".ts", ".mp4"))),
        "路径不存在或不是 FLV/TS/MP4，请重新输入。",
        default,
    )


def select_videos(folder: str, transcode: bool = False) -> list[str]:
    videos = source_videos(folder)
    print("该文件夹中的源视频（已排除渲染输出）：")
    defaults = []
    for index, video in enumerate(videos, 1):
        candidates = danmaku_candidates(video)
        if candidates or transcode:
            defaults.append(index - 1)
        sidecar = "✓ " + os.path.basename(candidates[0]) if candidates else "✗ 未找到"
        print(f"  [{index}] {os.path.basename(video)}    弹幕：{sidecar}")
    if not videos:
        print("没有符合条件的源视频。")
        return []
    picked = ask_numbers(
        "视频编号（空格分隔，回车选所有有弹幕的，n取消）：",
        len(videos),
        defaults,
        "请选择有效编号；没有匹配弹幕时需手动选编号。",
    )
    return [videos[index] for index in picked]


def ask_danmaku(video: str, allow_skip: bool = False) -> str | None:
    candidates = danmaku_candidates(video)
    if candidates:
        print(f"找到以下弹幕文件（{os.path.dirname(video)}）：")
        for index, path in enumerate(candidates, 1):
            print(
                f"  [{index}] {os.path.basename(path)}"
                + (" ← 默认" if index == 1 else "")
            )
    while True:
        prompt = (
            "弹幕编号或其他路径（回车选1" + ("，0跳过" if allow_skip else "") + "）："
            if candidates
            else f"{os.path.basename(video)} 未找到弹幕，请输入路径"
            + ("（回车跳过）" if allow_skip else "")
            + "："
        )
        value = input_path(prompt)
        if allow_skip and (value == "0" or (not value and not candidates)):
            return None
        if not value and candidates:
            return candidates[0]
        if value.isdigit():
            if 1 <= int(value) <= len(candidates):
                return candidates[int(value) - 1]
        elif os.path.isfile(value) and value.lower().endswith((".jsonl", ".ass")):
            return os.path.abspath(value)
        print("弹幕编号无效或文件不存在，请重新输入。")


def ask_task(config, folder: str):
    """Returns (task name or None for the global config, display label)."""
    name, _ = ask_task_name(config, folder)
    return name, f"任务「{name}」" if name else "全局配置"


def render_args(config, task, mode):
    if task:
        return config.get_render_args(task, mode)
    return config.global_config["render_args"][mode]


def confirm(tasks, source: str, label: str) -> bool:
    return confirm_lines(
        [f"任务：{source}（引擎：{label}）", "视频 | 弹幕 | 输出"]
        + [
            f'{task["video"]} | {task["danmaku"] or "不烧弹幕"} | {task["output"]}'
            + (" [覆盖]" if task.get("overwrite") else "")
            for task in tasks
        ]
    )


def selected_engine(options, args):
    if args.emoji:
        return EMOJI_ENGINE
    if args.no_emoji:
        return DEFAULT_ENGINE
    return options["engine"]


def render_settings(config, task, args, mode):
    """(engine name, render args). Transcoding re-encodes with the danmaku
    engine when it can, so a task's transcoded and danmaku outputs share one
    format and merge losslessly; otherwise the transcode settings apply."""
    overlay = render_args(config, task, "overlay")
    name = selected_engine(overlay, args)
    if mode != "transcode" or get_engine(name).plain:
        return name, overlay
    return TRANSCODE_ENGINE, render_args(config, task, "transcode")


def sort_tasks(tasks):
    def origin(task):
        if task["danmaku"] and task["danmaku"].lower().endswith(".jsonl"):
            try:
                with open(task["danmaku"], encoding="utf-8-sig") as stream:
                    return float(json.loads(stream.readline())["segment_start_time"])
            except (OSError, ValueError, KeyError):
                pass
        return os.path.getmtime(task["video"])

    return sorted(tasks, key=origin)


def interactive_tasks(config, args, mode):
    path = ask_video(args.input_dir or "")
    videos = select_videos(path, mode == "transcode") if os.path.isdir(path) else [path]
    tasks = []
    for video in videos:
        danmaku = (
            None if mode == "transcode" else ask_danmaku(video, os.path.isdir(path))
        )
        if danmaku or mode == "transcode":
            tasks.append({"video": video, "danmaku": danmaku})
    if not tasks:
        return [], None, None, "未选择视频"
    task_name, source = ask_task(config, os.path.dirname(tasks[0]["video"]))
    engine_name, options = render_settings(config, task_name, args, mode)
    engine = get_engine(engine_name)
    directory = args.output_dir or ask_output_folder(os.path.dirname(tasks[0]["video"]))
    protected = protect(
        *(path for task in tasks for path in (task["video"], task["danmaku"]))
    )
    selected = []
    suffix = "（转码后）" if mode == "transcode" else "（弹幕版）"
    for task in tasks:
        name = (
            os.path.splitext(os.path.basename(task["video"]))[0]
            + suffix
            + "."
            + options.get("format", "mp4")
        )
        output, overwrite = ask_output(directory, name, len(tasks) == 1, protected)
        if output:
            selected.append({**task, "output": output, "overwrite": overwrite})
    selected = sort_tasks(selected)
    label = engine.label + ("（不烧弹幕）" if mode == "transcode" and engine.overlay else "")
    return (
        (selected if selected and confirm(selected, source, label) else []),
        engine_name,
        options,
        source,
    )


def main():
    enable_console_escapes()
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default='configs/global.yml')
    parser.add_argument('--input_dir', type=str)
    parser.add_argument('--output_dir', type=str)
    parser.add_argument("--task", help="单文件模式读取的任务名，禁用任务也可离线渲染")
    parser.add_argument("--video")
    parser.add_argument("--danmaku")
    parser.add_argument("--output")
    parser.add_argument('--transcode', action='store_true', help='转码模式（不烧弹幕）')
    emoji_group = parser.add_mutually_exclusive_group()
    emoji_group.add_argument('--emoji', action='store_true', help='强制使用彩色 emoji 渲染引擎')
    emoji_group.add_argument('--no-emoji', action='store_true', help='强制使用普通 dmrender 引擎')
    args = parser.parse_args()

    # 控制台只出 WARNING 以上(引擎内部的常规 info 一律不刷屏),渲染状态用下面的简洁进度行显示
    logging.getLogger().setLevel(logging.INFO)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(logging.Formatter('[%(levelname)s] %(message)s'))
    logging.getLogger().addHandler(console_handler)

    config = Config(args.config)
    mode = 'transcode' if args.transcode else 'danmaku_render'

    single = any((args.task, args.video, args.danmaku, args.output))
    if single:
        required = (args.task, args.video, args.output) + (
            () if args.transcode else (args.danmaku,)
        )
        if not all(required):
            parser.error("单文件模式需要 --task --video --output（渲染还需要 --danmaku）")
        if not exists(args.video) or (args.danmaku and not exists(args.danmaku)):
            parser.error("视频或弹幕文件不存在")
        if exists(args.output):
            parser.error("输出已存在，请另选文件名")
        try:
            engine_name, options = render_settings(config, args.task, args, mode)
        except ValueError as error:
            parser.error(str(error))
        source = f"任务「{args.task}」(default → global → task)"
        selected = [
            {"video": args.video, "danmaku": args.danmaku, "output": args.output}
        ]
    else:
        selected, engine_name, options, source = interactive_tasks(config, args, mode)

    if not selected:
        print("没有开始渲染。")
        return 0
    engine = get_engine(engine_name)
    print(f'渲染参数来源: {source} → 渲染引擎: {engine_name}')
    TargetRender = engine.load()

    total = len(selected)
    n_ok = n_fail = 0
    layout_state = None
    cancelled = False
    for seq, task in enumerate(selected, 1):
        resolution = FFprobe.get_resolution(task['video'])
        video = VideoInfo(
            path=task['video'],
            dm_file_id=task['danmaku'],
            # JSONL布局和渲染必须共用视频包PTS时间线；直播FLV的
            # format.duration 可能短于最后一个实际视频包。
            duration=FFprobe.get_timeline_duration(task['video']),
            resolution=resolution,
        )
        output = os.path.abspath(task["output"])
        os.makedirs(os.path.dirname(output), exist_ok=True)

        label = f'[{seq}/{total}] {os.path.basename(task["video"])}'
        print(f'{label} 开始渲染')

        def on_progress(pct, *_, _label=label):
            show_progress(_label, pct)

        target_render = None
        output_work = None
        try:
            render_output = output
            if task.get("overwrite"):
                output_work = tempfile.TemporaryDirectory(
                    prefix=".render_only_", dir=os.path.dirname(output)
                )
                render_output = join(output_work.name, os.path.basename(output))
            target_args = options
            render_kwargs = {}
            layout_result = None
            if engine.overlay:
                layout_keys = {
                    'include_events',
                    'danmaku', 'gift', 'superchat', 'member',
                    'r2l', 'slide_left', 'slide_right',
                }
                target_args = {
                    key: value for key, value in options.items()
                    if key != 'engine' and key not in layout_keys
                }
                if not engine.raster:
                    for key in (
                        'emoji_pack', 'emoji_font', 'min_fps',
                    ):
                        target_args.pop(key, None)
                if not task['danmaku']:
                    # 不烧弹幕：用弹幕引擎原样重编码，输出与弹幕版同格式、可无损合并
                    render_kwargs['without_danmaku'] = True
                elif task['danmaku'].lower().endswith('.jsonl'):
                    from DMR.Render.layout_compiler import LayoutCompiler
                    layout_result = LayoutCompiler(
                        options,
                        engine=engine_name,
                        engine_config=options,
                    ).compile(
                        video=video,
                        events_path=task["danmaku"],
                        previous_state=layout_state,
                        work_dir=os.path.dirname(render_output) or None,
                    )
                    layout_state = layout_result.next_state
                    render_kwargs['render_plan'] = layout_result.render_plan_path
                    render_kwargs['canvas_resolution'] = (
                        layout_result.next_state.canvas_resolution
                    )
            target_render = TargetRender(**target_args)
            status, info = target_render.render_one(
                video=video,
                output=render_output,
                progress_cb=on_progress,
                **render_kwargs,
            )
            if status and output_work is not None:
                os.replace(info.path, output)
        except KeyboardInterrupt:
            if target_render is not None:
                target_render.stop()
            cancelled = True
            status, info = False, "用户停止"
        except Exception as e:
            status, info = False, e
        finally:
            if 'layout_result' in locals() and layout_result is not None:
                layout_result.cleanup()
            if output_work is not None:
                output_work.cleanup()
        print()   # 进度行换行

        if status:
            n_ok += 1
            print(f'{label} 完成 → {output}')
        else:
            n_fail += 1
            print(f'{label} 失败: {info}')
        if cancelled:
            break

    print(f'\n全部结束：成功 {n_ok} 个，失败 {n_fail} 个。')
    return 1 if n_fail else 0


if __name__ == '__main__':
    sys.exit(main())
