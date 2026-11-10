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
from DMR.utils import FFprobe, isvideo, VideoInfo


def resolve_render_args(config, input_dir, mode):
    """按输入文件夹自动匹配任务:哪个任务的 download_args.output_dir 就是这个文件夹,
    就用该任务合并后的 render_args(全局+任务覆盖,与主管线口径一致,emoji 开关也在其中)。
    没匹配到任何任务则用全局配置。返回 (render_args, 来源说明)。"""
    target = os.path.normcase(os.path.abspath(input_dir))
    for taskname, tconf in (config.task_configs or {}).items():
        out_dir = (tconf.get('download_args') or {}).get('output_dir')
        if not out_dir:
            continue
        if os.path.normcase(os.path.abspath(out_dir)) == target:
            root = tconf.get('render_args') or {}
            key = 'transcode' if mode == 'transcode' else 'overlay'
            ra = root.get(key)
            if ra:
                return ra, f'任务「{taskname}」'
    root = config.global_config.get('render_args', {})
    key = 'transcode' if mode == 'transcode' else 'overlay'
    ra = root.get(key) or {}
    return ra, '全局配置(未匹配到任务)'


def build_tasks(videos, mode, render_args, output_dir_arg):
    """扫描视频列表,生成渲染任务和忽略清单(已渲染过/缺弹幕的跳过)。"""
    tasks, ignores = [], []
    fmt = render_args.get('format', 'mp4')
    suffix = '（转码后）' if mode == 'transcode' else '（弹幕版）'

    for vname in videos:
        base = os.path.splitext(vname)[0]
        jsonl = base + '.jsonl'
        legacy_ass = base + '.ass'
        danmu = jsonl if exists(jsonl) else legacy_ass
        filename = os.path.splitext(os.path.basename(vname))[0] + f'{suffix}.{fmt}'
        output_dir = output_dir_arg or (os.path.dirname(vname) + suffix)
        output = join(output_dir, filename)

        if exists(output) and FFprobe.get_duration(output) - FFprobe.get_duration(vname) < 30:
            ignores.append((vname, f'已存在渲染产物 {output}，跳过.'))
            continue
        if mode != 'transcode' and not exists(danmu):
            ignores.append((vname, '不存在匹配的 JSONL 或 ASS 弹幕文件，跳过.'))
            continue
        segment_start = None
        if jsonl == danmu:
            try:
                with open(jsonl, 'r', encoding='utf-8') as fp:
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
        input_dir = args.input_dir or config.global_config.get('downloader_args', {}).get('output_dir', '直播回放')
        inp = input(f'请输入视频文件夹路径 (回车使用默认路径 {input_dir}): \n')
        input_dir = inp or input_dir
        render_args, source = resolve_render_args(config, input_dir, mode)
        videos = sorted(f for f in glob(join(input_dir, '*')) if isvideo(f))
        tasks, ignores = build_tasks(videos, mode, render_args, args.output_dir)
        for filename, message in ignores:
            print(f'忽略 {filename}: {message}')
        for index, item in enumerate(tasks):
            print(f'[{index}] {item["video"]} -> {item["output"]}')
        inp = input('输入视频编号（空格分隔），或回车渲染全部：\n')
        indices = [int(value) for value in inp.split()] if inp else range(len(tasks))
        selected = [tasks[index] for index in indices]

    if mode == 'transcode':
        engine_name = 'transcode'
    elif args.emoji:
        engine_name = 'emoji_dmrender'
    elif args.no_emoji:
        engine_name = 'dmrender'
    else:
        engine_name = render_args.get('engine', 'dmrender')
    if engine_name not in ('transcode', 'dmrender', 'emoji_dmrender', 'gpu_render'):
        parser.error(f'不支持的引擎: {engine_name}')
    print(f'渲染参数来源: {source} → 渲染引擎: {engine_name}')

    if mode == 'transcode':
        from DMR.Render.transcode import Transcoder as TargetRender
    elif engine_name == 'gpu_render':
        from DMR.Render.gpu_render import GpuRender as TargetRender
    elif engine_name == 'emoji_dmrender':
        from DMR.Render.emoji_dmrender import EmojiDmRender as TargetRender
    else:
        from DMR.Render.dmrender import DmRender as TargetRender

    total = len(selected)
    n_ok = n_fail = 0
    layout_state = None
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
        output = task['output']
        os.makedirs(os.path.dirname(output), exist_ok=True)

        label = f'[{seq}/{total}] {os.path.basename(task["video"])}'
        print(f'{label} 开始渲染')

        def on_progress(pct, *_, _label=label):
            print(f'\r{_label} 进度 {pct}%   ', end='', flush=True)

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
                if engine_name == 'dmrender':
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
            target_render.stop()
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

    print(f'\n全部结束：成功 {n_ok} 个，失败 {n_fail} 个。')
    return 1 if n_fail else 0


if __name__ == '__main__':
    sys.exit(main())
