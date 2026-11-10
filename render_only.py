"""渲染专用脚本（离线批量）：对文件夹里的视频和同名 JSONL/ASS 渲染或转码。

引擎选择与主管线一致（见 liveevents._check_for_render）：
  - render_args.overlay.engine 选择 dmrender / emoji_dmrender / gpu_render；
  - JSONL 由 LayoutCompiler 依次生成每段 RenderPlan，并维护跨段 track；
  - 同名 ASS 仍作为旧文件兼容输入；
  - --emoji / --no-emoji 可强制覆盖配置。

用法：
    python render_only.py [--config configs/global.yml] [--input_dir 路径]
                          [--output_dir 路径] [--transcode] [--emoji | --no-emoji]
"""
import argparse
import json
import os
import sys
import logging
from glob import glob
from os.path import exists, join

sys.path.append('..')
sys.path.append('.')

from DMR.Config import Config
from DMR.Render import get_engine, DEFAULT_ENGINE, EMOJI_ENGINE, TRANSCODE_ENGINE
from DMR.utils import FFprobe, isvideo, VideoInfo


def input_path(prompt):
    """Accept a pasted Windows path, including surrounding quotes."""
    return input(prompt).strip().strip('"').strip("'")


def show_progress(label, pct):
    """Erase the previous line before writing a shorter progress value."""
    print(f'\r\x1b[2K{label} 进度 {float(pct):.1f}%', end='', flush=True)


def enable_console_escapes():
    """Enable erase-line escapes in native Windows console sessions."""
    if os.name != 'nt' or not sys.stdout.isatty():
        return
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.windll.kernel32
    kernel.GetStdHandle.restype = wintypes.HANDLE
    kernel.GetConsoleMode.argtypes = [wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
    kernel.SetConsoleMode.argtypes = [wintypes.HANDLE,wintypes.DWORD]
    handle = kernel.GetStdHandle(-11)
    mode = wintypes.DWORD()
    if kernel.GetConsoleMode(handle,ctypes.byref(mode)):
        kernel.SetConsoleMode(handle,mode.value | 0x0004)


def find_danmaku(video):
    base = os.path.splitext(video)[0]
    return next((path for path in (base + '.jsonl', base + '_augmented.jsonl', base + '.ass')
                 if exists(path)), None)


def ask_danmaku(video, allow_skip=False):
    """Show the discovered sidecar and require an explicit confirmation."""
    found = find_danmaku(video)
    while True:
        if found:
            value = input_path(f'弹幕文件：{found}\n回车确认，或输入其他弹幕路径：') or found
        else:
            hint = '（回车跳过此视频）' if allow_skip else ''
            value = input_path(f'{os.path.basename(video)} 未找到同名弹幕，请输入 JSONL/ASS 路径{hint}：')
        if not value and allow_skip:
            return None
        if os.path.isfile(value) and value.lower().endswith(('.jsonl','.ass')):
            return value
        print('弹幕文件不存在或格式不是 JSONL/ASS，请重新输入。')


def choose_render_args(config, mode):
    """Offer every task, including disabled tasks; global is an explicit choice."""
    from DMR.utils import filename_to_taskname
    names = [filename_to_taskname(path) for path in config.task_config_files()]
    print('任务配置：[0] 全局配置（回车）')
    for index, name in enumerate(names, 1):
        print(f'[{index}] {name}')
    while True:
        value = input('请选择任务编号：').strip()
        if not value or value == '0':
            return config.global_config['render_args'][mode], '全局配置（用户选择）'
        if value.isdigit() and 1 <= int(value) <= len(names):
            name = names[int(value)-1]
            return config.get_render_args(name, mode), f'任务「{name}」(default → global → task)'
        print('任务编号无效，请重新选择。')


def interactive_tasks(config, args, mode):
    default = args.input_dir or ''
    while True:
        path = input_path(f'请输入视频文件路径或文件夹{f"（回车使用 {default}）" if default else ""}：') or default
        if os.path.isdir(path) or (os.path.isfile(path) and isvideo(path)):
            break
        print('视频文件或文件夹不存在，请重新输入。')
    batch = os.path.isdir(path)
    danmaku = None if batch or mode == 'transcode' else ask_danmaku(path)
    options, source = choose_render_args(config, 'transcode' if mode == 'transcode' else 'overlay')
    directory = os.path.abspath(path if batch else os.path.dirname(path) or '.')
    output_dir = args.output_dir or input_path(f'输出文件夹（回车使用 {directory}）：') or directory
    if batch:
        videos = sorted(f for f in glob(join(path, '*')) if isvideo(f))
        tasks, ignores = build_tasks(videos, mode, options, output_dir, missing_danmaku=lambda video: ask_danmaku(video,True))
        for filename, message in ignores:
            print(f'忽略 {filename}: {message}')
        if not tasks:
            return [], options, source
        for index, task in enumerate(tasks):
            print(f'[{index}] {task["video"]} -> {task["output"]}')
        while True:
            value = input('输入视频编号（空格分隔），或回车渲染全部：').strip()
            try:
                indices = [int(v) for v in value.split()] if value else range(len(tasks))
                if any(i < 0 or i >= len(tasks) for i in indices):
                    raise ValueError()
                return [tasks[i] for i in indices], options, source
            except ValueError:
                print('视频编号无效，请重新输入。')
    suffix = '（转码后）' if mode == 'transcode' else '（弹幕版）'
    name = os.path.splitext(os.path.basename(path))[0] + suffix + '.mp4'
    while True:
        filename = input_path(f'输出文件名（回车使用 {name}）：') or name
        output = join(output_dir, filename)
        if os.path.basename(filename) != filename:
            print('此处只输入文件名，目录请在上一步指定。')
        elif exists(output):
            print('输出已存在，请另选文件名。')
        else:
            return [{'video':path,'danmaku':danmaku,'output':output}], options, source


def build_tasks(videos, mode, render_args, output_dir_arg, missing_danmaku=None):
    """扫描视频列表,生成渲染任务和忽略清单(已渲染过/缺弹幕的跳过)。"""
    tasks, ignores = [], []
    fmt = render_args.get('format', 'mp4')
    suffix = '（转码后）' if mode == 'transcode' else '（弹幕版）'

    for vname in videos:
        base = os.path.splitext(vname)[0]
        jsonl = base + '.jsonl'
        legacy_ass = base + '.ass'
        danmu = find_danmaku(vname)
        filename = os.path.splitext(os.path.basename(vname))[0] + f'{suffix}.{fmt}'
        output_dir = output_dir_arg or (os.path.dirname(vname) + suffix)
        output = join(output_dir, filename)

        if exists(output) and FFprobe.get_duration(output) - FFprobe.get_duration(vname) < 30:
            ignores.append((vname, f'已存在渲染产物 {output}，跳过.'))
            continue
        if mode != 'transcode' and not danmu and missing_danmaku:
            danmu = missing_danmaku(vname)
        if mode != 'transcode' and not danmu:
            ignores.append((vname, '不存在匹配的 JSONL 或 ASS 弹幕文件，跳过.'))
            continue
        segment_start = None
        if danmu and danmu.lower().endswith('.jsonl'):
            try:
                with open(danmu, 'r', encoding='utf-8') as fp:
                    segment_start = float(json.loads(fp.readline())['segment_start_time'])
            except Exception:
                pass
        tasks.append({
            'video': vname,
            'danmaku': danmu,
            'output': output,
            'segment_start': segment_start,
        })
    # “同一分钟(2)”按文件名排序可能排在无后缀文件之前。JSONL 输入必须
    # 依物理起点编译，否则 track 会倒序消费。
    tasks.sort(key=lambda item: (
        item['segment_start'] is None,
        item['segment_start'] if item['segment_start'] is not None
        else os.path.getmtime(item['video']),
    ))
    return tasks, ignores


def main():
    enable_console_escapes()
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default='configs/global.yml')
    parser.add_argument('--input_dir', type=str)
    parser.add_argument('--output_dir', type=str)
    parser.add_argument('--task', help='单文件模式读取的任务名，禁用任务也可离线渲染')
    parser.add_argument('--video')
    parser.add_argument('--danmaku')
    parser.add_argument('--output')
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
        if not all((args.task, args.video, args.danmaku, args.output)):
            parser.error('单文件模式需要 --task --video --danmaku --output')
        if not exists(args.video) or not exists(args.danmaku):
            parser.error('视频或弹幕文件不存在')
        if exists(args.output):
            parser.error('输出已存在，请另选文件名')
        render_args = config.get_render_args(args.task, 'transcode' if args.transcode else 'overlay')
        source = f'任务「{args.task}」(default → global → task)'
        selected = [{'video': args.video, 'danmaku': args.danmaku, 'output': args.output}]
    else:
        selected, render_args, source = interactive_tasks(config, args, mode)

    if mode == 'transcode':
        engine_name = TRANSCODE_ENGINE
    elif args.emoji:
        engine_name = EMOJI_ENGINE
    elif args.no_emoji:
        engine_name = DEFAULT_ENGINE
    else:
        engine_name = render_args['engine']
    try:
        engine = get_engine(engine_name)
    except ValueError as error:
        parser.error(str(error))
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
        output = os.path.abspath(task['output'])
        os.makedirs(os.path.dirname(output), exist_ok=True)

        label = f'[{seq}/{total}] {os.path.basename(task["video"])}'
        print(f'{label} 开始渲染')

        def on_progress(pct, *_, _label=label):
            show_progress(_label,pct)

        target_render = None
        try:
            target_args = render_args
            render_kwargs = {}
            layout_result = None
            if mode != 'transcode':
                layout_keys = {
                    'include_events',
                    'danmaku', 'gift', 'superchat', 'member',
                    'r2l', 'slide_left', 'slide_right',
                }
                target_args = {
                    key: value for key, value in render_args.items()
                    if key != 'engine' and key not in layout_keys
                }
                if not engine.raster:
                    for key in (
                        'emoji_pack', 'emoji_font', 'min_fps',
                    ):
                        target_args.pop(key, None)
                if task['danmaku'].lower().endswith('.jsonl'):
                    from DMR.Render.layout_compiler import LayoutCompiler
                    layout_result = LayoutCompiler(
                        render_args,
                        engine=engine_name,
                        engine_config=render_args,
                    ).compile(
                        video=video,
                        events_path=task['danmaku'],
                        previous_state=layout_state,
                        work_dir=os.path.dirname(output) or None,
                    )
                    layout_state = layout_result.next_state
                    render_kwargs['render_plan'] = layout_result.render_plan_path
                    render_kwargs['canvas_resolution'] = (
                        layout_result.next_state.canvas_resolution
                    )
            target_render = TargetRender(**target_args)
            status, info = target_render.render_one(
                video=video,
                output=output,
                progress_cb=on_progress,
                **render_kwargs,
            )
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
