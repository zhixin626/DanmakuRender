"""Console prompts shared by render_only / merge_only / upload_only / cover_only.

Every question works the same way: paths may be dragged in (quotes are
stripped), menus are numbered, Enter takes the marked default, and nothing
runs before a final confirmation.
"""

import os
from os.path import exists, join

import yaml

from .utils import filename_to_taskname, merge_dict

# Folder suffix of each derived video type, as written by LiveEvents.
OUTPUT_SUFFIXES = {"（弹幕版）": "dm_video", "（转码后）": "src_video"}


def input_path(prompt):
    """Accept a pasted Windows path, including surrounding quotes."""
    return input(prompt).strip().strip('"').strip("'")


def ask_path(label, is_valid, error, default=""):
    hint = f"，回车：{default}" if default else ""
    while True:
        path = input_path(f"{label}（可拖入{hint}）：") or default
        if path and is_valid(path):
            return os.path.abspath(path)
        print(error)


def ask_choice(title, labels, default=0, first=1):
    """Single choice from a numbered menu; returns the 0-based index."""
    print(title)
    for index, label in enumerate(labels):
        print(f"  [{index + first}] {label}" + (" ← 默认" if index == default else ""))
    while True:
        value = input(f"编号（回车选{default + first}）：").strip()
        if not value:
            return default
        if value.isdigit() and 0 <= int(value) - first < len(labels):
            return int(value) - first
        print("编号无效，请重新输入。")


def ask_numbers(prompt, count, default, error="编号无效，请重新输入。"):
    """Several 1-based numbers in the typed order; Enter keeps `default`
    (0-based indices), n cancels. Returns 0-based indices."""
    while True:
        value = input(prompt).strip()
        if value.lower() == "n":
            return []
        try:
            picked = (
                [int(v) - 1 for v in dict.fromkeys(value.split())]
                if value
                else list(default)
            )
        except ValueError:
            picked = []
        if picked and all(0 <= index < count for index in picked):
            return picked
        print(error)


def ask_output_folder(default):
    return ask_path(
        "输出文件夹",
        lambda path: not exists(path) or os.path.isdir(path),
        "该路径是文件，请输入文件夹。",
        default,
    )


def ask_output(directory, name, ask_name, protected):
    """Pick a file name inside `directory`; existing files may be overwritten,
    renamed or skipped. Returns (path, overwrite) or (None, False)."""
    extension = os.path.splitext(name)[1] or ".mp4"
    while True:
        if ask_name:
            name = input_path(f"输出文件名（回车：{name}）：") or name
            ask_name = False
        if (
            not name
            or os.path.basename(name) != name
            or any(char in name for char in '<>:"/\\|?*')
        ):
            print("文件名无效，只填写文件名。")
            ask_name = True
            continue
        if not os.path.splitext(name)[1]:
            name += extension
        output = os.path.abspath(join(directory, name))
        if os.path.normcase(os.path.realpath(output)) in protected:
            print("输出不能覆盖输入文件，请改名。")
            ask_name = True
            continue
        if not exists(output):
            return output, False
        print(f"输出已存在：{output}")
        choice = input("[1] 覆盖  [2] 改名  [3] 跳过：").strip()
        if choice == "1":
            return output, True
        if choice == "2":
            stem, suffix = os.path.splitext(name)
            candidate = 2
            while True:
                suggested = f"{stem}_{candidate}{suffix}"
                suggested_path = os.path.abspath(join(directory, suggested))
                if (
                    not exists(suggested_path)
                    and os.path.normcase(os.path.realpath(suggested_path))
                    not in protected
                ):
                    name = suggested
                    break
                candidate += 1
            ask_name = True
        elif choice == "3":
            return None, False
        else:
            print("请输入1、2或3。")


def protect(*paths):
    """Normalised paths an output must never overwrite."""
    return {os.path.normcase(os.path.realpath(path)) for path in paths if path}


def confirm(lines):
    print("──────── 确认 ────────")
    for line in lines:
        print(line)
    while True:
        value = input("回车开始，输入n取消：").strip().lower()
        if value in ("", "y", "1"):
            return True
        if value in ("n", "2"):
            return False
        print("回车开始，或输入n取消。")


def read_task_file(path):
    with open(path, encoding="utf-8") as stream:
        return yaml.safe_load(stream) or {}


def task_directories(config):
    """(task name, recording folder, config file) of every task, enabled or not."""
    tasks = []
    for filename in config.task_config_files():
        task = read_task_file(filename).get("download_args") or {}
        defaults = config.global_config.get("download_args", {}).get(
            task.get("dltype", "live"), {}
        )
        tasks.append(
            (
                filename_to_taskname(filename),
                merge_dict(defaults, task).get("output_dir"),
                filename,
            )
        )
    return tasks


def default_task(tasks, folder):
    """1-based index of the task whose recording folder holds `folder`
    (its 弹幕版/转码后 folders included), or 0 when none or ambiguous."""
    for suffix in OUTPUT_SUFFIXES:
        if folder.endswith(suffix):
            folder = folder[: -len(suffix)]
    folder = os.path.normcase(os.path.realpath(folder))
    matches = []
    for index, (_, directory, _) in enumerate(tasks, 1):
        if not directory:
            continue
        directory = os.path.normcase(os.path.realpath(directory))
        if folder == directory or folder.startswith(directory + os.sep):
            matches.append((len(directory), index))
    if not matches:
        return 0
    longest = max(length for length, _ in matches)
    best = [index for length, index in matches if length == longest]
    return best[0] if len(best) == 1 else 0


def ask_task(config, folder):
    """Returns (task name, config file), or (None, None) for the global config."""
    tasks = task_directories(config)
    index = ask_choice(
        "任务配置：",
        ["全局配置"] + [name for name, _, _ in tasks],
        default_task(tasks, folder),
        first=0,
    )
    if index == 0:
        return None, None
    name, _, filename = tasks[index - 1]
    return name, filename
