"""把一个或多个 JSONL 弹幕文件渲染到对应视频上。

交互模式：
    python render_jsonl_emoji.py

程序会依次询问第一个视频、第一个 JSONL、第二个视频、第二个 JSONL……
在“下一个视频”处直接回车结束输入，然后为本批视频统一选择任务配置，
再选择实际输出哪些片段，最后再次回车确认开始渲染。未选择的前置
片段仍会推进跨段布局状态，但不会编码输出视频；状态预计算默认从
最早选中片段的前两段开始。

单任务命令仍然可用（JSONL 和视频的先后顺序可以互换）：
    python render_jsonl_emoji.py 输入.jsonl 输入.mp4 输出.mp4 --task-config 峰哥

未选择任务配置时使用全局 Overlay；选择后按 default → global → 单任务配置生效。
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

LAYOUT_KEYS = {
    "include_events",
    "danmaku",
    "gift",
    "superchat",
    "member",
    "r2l",
    "slide_left",
    "slide_right",
}
VIDEO_EXTENSIONS = {
    ".mp4",
    ".flv",
    ".mkv",
    ".ts",
    ".mov",
    ".m4v",
    ".webm",
    ".avi",
}
STATE_LOOKBACK_SEGMENTS = 2


def _clean_path_text(value: str) -> str:
    value = str(value or "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        value = value[1:-1]
    return value.strip()


def resolve_existing_file(value: str) -> Path:
    path = Path(_clean_path_text(value)).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"文件不存在: {path}")
    return path


def existing_file(value: str) -> Path:
    try:
        return resolve_existing_file(value)
    except FileNotFoundError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def positive_min_fps(value: str) -> float:
    try:
        fps = float(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("fps must be a positive number") from exc
    if not math.isfinite(fps) or fps <= 0:
        raise argparse.ArgumentTypeError("fps must be a positive number")
    return fps


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="将一个或多个 JSONL 编译成 RenderPlan 并渲染视频。",
    )
    parser.add_argument(
        "input_a",
        nargs="?",
        type=existing_file,
        help="输入 JSONL 或视频；全部省略时进入交互模式",
    )
    parser.add_argument(
        "input_b",
        nargs="?",
        type=existing_file,
        help="另一个输入文件",
    )
    parser.add_argument(
        "output_video",
        nargs="?",
        type=Path,
        help="单任务命令行模式的输出视频",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=PROJECT_ROOT / "configs" / "global.yml",
        help="配置文件，默认使用项目的 configs/global.yml",
    )
    parser.add_argument(
        "--task-config",
        help="命令行模式使用的任务配置名，例如“峰哥”；省略时仅使用全局配置",
    )
    parser.add_argument(
        "--min-fps",
        type=positive_min_fps,
        help="最低输出帧率，例如 30 或 60；只补低帧率源，不会降低高帧率源",
    )
    args = parser.parse_args()

    supplied = (args.input_a, args.input_b, args.output_video)
    if any(value is not None for value in supplied) and not all(
        value is not None for value in supplied
    ):
        parser.error("命令行模式必须同时提供视频、JSONL和输出视频")
    return args


def automatic_output_path(video: Path) -> Path:
    output_dir = video.parent.parent / f"{video.parent.name}（弹幕版）"
    return output_dir / f"{video.stem}（弹幕版）.mp4"


def read_segment_start_time(jsonl: Path) -> float:
    try:
        with jsonl.open("r", encoding="utf-8") as source:
            header = json.loads(source.readline())
        return float(header.get("segment_start_time", 0) or 0)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return 0.0


def discover_folder_tasks(folder: Path) -> tuple[list[dict], list[str]]:
    videos_by_stem = {}
    jsonls_by_stem = {}
    ignored = []

    for path in folder.iterdir():
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        stem = path.stem.casefold()
        if suffix in VIDEO_EXTENSIONS:
            videos_by_stem.setdefault(stem, []).append(path.resolve())
        elif suffix == ".jsonl":
            if path.name.casefold() == "gifts_statistics.jsonl":
                ignored.append(f"统计文件：{path.name}")
            else:
                jsonls_by_stem.setdefault(stem, []).append(path.resolve())

    tasks = []
    unmatched = list(ignored)
    all_stems = sorted(set(videos_by_stem) | set(jsonls_by_stem))
    for stem in all_stems:
        videos = videos_by_stem.get(stem, [])
        jsonls = jsonls_by_stem.get(stem, [])
        if len(videos) == 1 and len(jsonls) == 1:
            video = videos[0]
            jsonl = jsonls[0]
            tasks.append(
                {
                    "video": video,
                    "jsonl": jsonl,
                    "output": automatic_output_path(video),
                    "_segment_start_time": read_segment_start_time(jsonl),
                }
            )
            continue

        names = [path.name for path in videos + jsonls]
        if videos and jsonls:
            unmatched.append(f"同名文件有歧义：{'、'.join(names)}")
        elif videos:
            unmatched.append(f"缺少同名JSONL：{'、'.join(names)}")
        else:
            unmatched.append(f"缺少同名视频：{'、'.join(names)}")

    tasks.sort(
        key=lambda task: (
            task["_segment_start_time"] <= 0,
            task["_segment_start_time"],
            task["video"].name.casefold(),
        )
    )
    for task in tasks:
        task.pop("_segment_start_time", None)
    return tasks, unmatched


def confirm_folder_tasks(
    folder: Path,
    tasks: list[dict],
    unmatched: list[str],
) -> bool:
    print(f"\n文件夹识别结果：{folder}")
    if tasks:
        for number, task in enumerate(tasks, 1):
            print(f"[{number}] 视频：{task['video'].name}")
            print(f"    JSONL：{task['jsonl'].name}")
    else:
        print("没有找到同名的视频与JSONL。")

    if unmatched:
        print("\n未自动配对：")
        for item in unmatched:
            print(f"  - {item}")

    if not tasks:
        return False
    answer = input(
        "\n以上自动分类是否正确？直接回车确认，输入其他内容取消：\n"
    )
    return not answer.strip()


def pair_inputs(input_a: Path, input_b: Path, output: Path) -> dict:
    inputs = (input_a, input_b)
    jsonl_inputs = [path for path in inputs if path.suffix.lower() == ".jsonl"]
    video_inputs = [path for path in inputs if path.suffix.lower() != ".jsonl"]
    if len(jsonl_inputs) != 1 or len(video_inputs) != 1:
        raise ValueError("两个输入必须分别是一个JSONL和一个视频文件")
    video = video_inputs[0]
    output = output.expanduser().resolve()
    if output == video:
        raise ValueError("输出视频不能覆盖输入视频")
    return {"video": video, "jsonl": jsonl_inputs[0], "output": output}


def resolve_task_config_name(config, value: str | None) -> str | None:
    value = _clean_path_text(value)
    if not value or value.lower() in {"0", "global", "none"} or value == "仅全局":
        return None

    available = config.get_task_names()
    if value in available:
        return value

    normalized = Path(value).stem.split("-", 1)[-1]
    matches = [name for name in available if name == normalized]
    if len(matches) == 1:
        return matches[0]

    raise ValueError(
        f"找不到已启用的任务配置 {value!r}；可选："
        + "、".join(available)
    )


def print_task_config_choices(config) -> list[str]:
    names = config.get_task_names()
    print("\n可用任务配置：")
    print("  [0] 仅全局配置")
    for number, name in enumerate(names, 1):
        print(f"  [{number}] {name}")
    return names


def choose_task_config(names: list[str]) -> str | None:
    while True:
        raw = input(
            "请选择本批视频使用的任务配置"
            "（输入序号或名称，直接回车使用仅全局配置）：\n"
        )
        value = _clean_path_text(raw)
        if not value or value == "0" or value == "仅全局":
            return None
        if value.isdigit():
            number = int(value)
            if 1 <= number <= len(names):
                return names[number - 1]
        if value in names:
            return value
        normalized = Path(value).stem.split("-", 1)[-1]
        if normalized in names:
            return normalized
        print("任务配置不存在，请输入上面显示的序号或名称。")


def choose_render_indices(tasks: list[dict]) -> set[int]:
    if len(tasks) == 1:
        return {1}
    print("\n已输入的视频：")
    for number, task in enumerate(tasks, 1):
        print(f"  [{number}] {task['video'].name}")
    while True:
        raw = input(
            "\n请输入需要实际渲染的视频编号"
            "（如 2 或 2 3，直接回车全部渲染）：\n"
        ).replace("，", " ").replace(",", " ")
        if not raw.strip():
            return set(range(1, len(tasks) + 1))
        try:
            selected = {int(value) for value in raw.split()}
        except ValueError:
            selected = set()
        if selected and min(selected) >= 1 and max(selected) <= len(tasks):
            return selected
        print(f"请输入 1 到 {len(tasks)} 之间的一个或多个编号。")


def mark_task_actions(tasks: list[dict], selected: set[int]) -> None:
    first_processed = max(1, min(selected) - STATE_LOOKBACK_SEGMENTS)
    last_processed = max(selected)
    for number, task in enumerate(tasks, 1):
        task["render"] = number in selected
        task["process"] = first_processed <= number <= last_processed


def collect_interactive_tasks(config) -> list[dict]:
    print("请按直播录像的先后顺序输入视频和对应JSONL。")
    print("路径可以直接拖入窗口；在“下一个视频”处直接回车结束输入。")
    tasks = []
    index = 1
    while True:
        raw_video = input(
            f"\n请输入第{index}个视频路径或包含视频和JSONL的文件夹"
            f"{'（直接回车结束）' if tasks else ''}：\n"
        )
        if not _clean_path_text(raw_video):
            if tasks:
                break
            print("至少需要输入一个视频。")
            continue

        input_path = Path(_clean_path_text(raw_video)).expanduser().resolve()
        if input_path.is_dir():
            folder_tasks, unmatched = discover_folder_tasks(input_path)
            if confirm_folder_tasks(input_path, folder_tasks, unmatched):
                tasks.extend(folder_tasks)
                break
            print("已取消本次文件夹导入，请重新输入。")
            continue
        try:
            video = resolve_existing_file(raw_video)
        except FileNotFoundError as exc:
            print(exc)
            continue
        if video.suffix.lower() == ".jsonl":
            print("这里需要输入视频文件，不是JSONL。")
            continue

        raw_jsonl = input(f"请输入第{index}个JSONL路径：\n")
        if not _clean_path_text(raw_jsonl):
            print("JSONL不能为空，请重新输入这一组。")
            continue
        try:
            jsonl = resolve_existing_file(raw_jsonl)
        except FileNotFoundError as exc:
            print(exc)
            continue
        if jsonl.suffix.lower() != ".jsonl":
            print("第二个文件必须是JSONL，请重新输入这一组。")
            continue

        tasks.append(
            {
                "video": video,
                "jsonl": jsonl,
                "output": automatic_output_path(video),
            }
        )
        index += 1

    task_config_names = print_task_config_choices(config)
    task_config_name = choose_task_config(task_config_names)
    for task in tasks:
        task["task_config"] = task_config_name

    selected = choose_render_indices(tasks)
    mark_task_actions(tasks, selected)

    print("\n即将按以下顺序处理：")
    for number, task in enumerate(tasks, 1):
        if task["render"]:
            action = "渲染输出"
        elif task["process"]:
            action = "仅计算前置状态"
        else:
            action = "不处理"
        print(f"[{number}] 视频：{task['video']}")
        print(f"    JSONL：{task['jsonl']}")
        print(f"    配置：{task['task_config'] or '仅全局配置'}")
        print(f"    操作：{action}")
        if task["render"]:
            print(f"    输出：{task['output']}")

    confirmation = input("\n请检查以上内容；再次直接回车确认开始，输入其他内容取消：\n")
    if confirmation.strip():
        print("已取消。")
        return []
    return tasks


def main() -> int:
    args = parse_args()
    config_path = args.config.expanduser().resolve()
    if not config_path.is_file():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")

    # Config 内部的默认配置路径相对于项目根目录。
    os.chdir(PROJECT_ROOT)
    try:
        from DMR.Config import Config
        from DMR.Render.emoji_dmrender import EmojiDmRender
        from DMR.Render.layout_compiler import LayoutCompiler
        from DMR.utils import FFprobe, VideoInfo
    except ModuleNotFoundError as exc:
        missing = exc.name or "未知模块"
        raise RuntimeError(
            f"当前Python环境缺少项目依赖 {missing!r}。\n"
            "请先执行：\n"
            f'  python -m pip install -r "{PROJECT_ROOT / "requirements.txt"}"'
        ) from exc

    config = Config(str(config_path))
    if args.input_a is None:
        if args.task_config:
            raise ValueError("--task-config 只用于命令行单任务模式")
        tasks = collect_interactive_tasks(config)
        if not tasks:
            return 0
    else:
        task = pair_inputs(args.input_a, args.input_b, args.output_video)
        task["task_config"] = resolve_task_config_name(config, args.task_config)
        task["render"] = True
        task["process"] = True
        tasks = [task]

    print("渲染方式：整段原视频单路连续解码，不切段")

    previous_states = {}
    total_tasks = len(tasks)
    total_renders = sum(bool(task["render"]) for task in tasks)
    completed_renders = 0
    for index, task in enumerate(tasks, 1):
        if not task["process"]:
            continue
        input_video = task["video"]
        jsonl = task["jsonl"]
        output_video = task["output"]
        task_config_name = task["task_config"]
        should_render = bool(task["render"])
        if should_render:
            output_video.parent.mkdir(parents=True, exist_ok=True)

        if task_config_name:
            selected_config = config.get_task_config(task_config_name)
            render_args = dict(
                selected_config.get("render_args", {}).get("overlay", {})
            )
        else:
            render_args = dict(
                config.global_config.get("render_args", {}).get("overlay", {})
            )
        if args.min_fps is not None:
            render_args["min_fps"] = args.min_fps
        render_args["engine"] = "emoji_dmrender"

        engine_args = {
            key: value
            for key, value in render_args.items()
            if key != "engine" and key not in LAYOUT_KEYS
        }
        compiler = LayoutCompiler(
            render_args,
            engine="emoji_dmrender",
            engine_config=render_args,
        )

        resolution = FFprobe.get_resolution(str(input_video))
        duration = FFprobe.get_timeline_duration(str(input_video))
        if not resolution or not all(resolution):
            raise RuntimeError(f"无法读取视频分辨率: {input_video}")
        if duration <= 0:
            raise RuntimeError(f"无法读取视频时间线长度: {input_video}")

        video = VideoInfo(
            path=str(input_video),
            dm_file_id=str(jsonl),
            duration=duration,
            resolution=resolution,
        )
        label = f"[{index}/{total_tasks}] {input_video.name}"
        print(f"\n{label}")
        print(f"输入弹幕：{jsonl}")
        print(f"任务配置：{task_config_name or '仅全局配置'}")
        print(
            f"操作：{'渲染输出' if should_render else '仅计算前置状态'}"
        )
        if should_render:
            print(f"输出视频：{output_video}")

        layout_result = None
        try:
            layout_result = compiler.compile(
                video=video,
                events_path=str(jsonl),
                previous_state=previous_states.get(task_config_name),
                work_dir=str(output_video.parent) if should_render else None,
            )
            previous_states[task_config_name] = layout_result.next_state
            canvas = layout_result.next_state.canvas_resolution
            resolution_text = f"{resolution[0]}x{resolution[1]}"
            if tuple(canvas) != tuple(resolution):
                resolution_text += (
                    f" → 1080p渲染画布 {canvas[0]}x{canvas[1]}"
                )
            print(f"分辨率 {resolution_text}，时长 {duration:.3f}s")
            print(
                f"RenderPlan已生成：布局 {layout_result.event_count} 条，"
                f"丢弃 {layout_result.dropped_count} 条"
            )
            if not should_render:
                print(f"{label} 前置状态已计算，不编码视频")
                continue

            renderer = EmojiDmRender(**engine_args)
            last_progress = -1

            def show_progress(percent: int, *_unused) -> None:
                nonlocal last_progress
                percent = int(percent)
                if percent != last_progress:
                    print(
                        f"\r{label} 渲染进度：{percent:3d}%",
                        end="",
                        flush=True,
                    )
                    last_progress = percent

            success, result = renderer.render_one(
                video=video,
                output=str(output_video),
                progress_cb=show_progress,
                render_plan=layout_result.render_plan_path,
                canvas_resolution=canvas,
            )
            if last_progress >= 0:
                print()
            if not success:
                print(f"{label} 渲染失败：{result}", file=sys.stderr)
                return 1

            actual_output = Path(getattr(result, "path", output_video))
            completed_renders += 1
            print(f"{label} 渲染完成：{actual_output}")
        finally:
            if layout_result is not None:
                layout_result.cleanup()

    print(
        f"\n全部完成，共成功渲染 {completed_renders}/{total_renders} 个视频。"
    )
    return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="[%(levelname)s] %(message)s",
    )
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n已取消渲染。", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        logging.exception("渲染失败: %s", exc)
        raise SystemExit(1)
