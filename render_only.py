"""渲染专用脚本（离线批量）：对文件夹里的 视频+同名.ass 逐个渲染弹幕版 / 转码。

引擎选择与主管线一致（见 liveevents._check_for_render）：
  - 配置 render_args.dmrender.emoji: true 时用彩色 emoji 引擎
    （EmojiDmRender → DMR/Render/emoji_render_mt.py 多核精灵渲染）；
  - 否则用普通 dmrender（libass 单遍烧录）；
  - --emoji / --no-emoji 可强制覆盖配置。

用法：
    python render_only.py [--config configs/global.yml] [--input_dir 路径]
                          [--output_dir 路径] [--transcode] [--emoji | --no-emoji]
"""
import argparse
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
            ra = (tconf.get('render_args') or {}).get(mode)
            if ra:
                return ra, f'任务「{taskname}」'
    ra = config.global_config.get('render_args', {}).get(mode) or {}
    return ra, '全局配置(未匹配到任务)'


def build_tasks(videos, mode, render_args, output_dir_arg):
    """扫描视频列表,生成渲染任务和忽略清单(已渲染过/缺弹幕的跳过)。"""
    tasks, ignores = [], []
    fmt = render_args.get('format', 'mp4')
    suffix = '（转码后）' if mode == 'transcode' else '（弹幕版）'

    for vname in videos:
        danmu = os.path.splitext(vname)[0] + '.ass'
        filename = os.path.splitext(os.path.basename(vname))[0] + f'{suffix}.{fmt}'
        output_dir = output_dir_arg or (os.path.dirname(vname) + suffix)
        output = join(output_dir, filename)

        if exists(output) and FFprobe.get_duration(output) - FFprobe.get_duration(vname) < 30:
            ignores.append((vname, f'已存在渲染产物 {output}，跳过.'))
            continue
        if mode != 'transcode' and not exists(danmu):
            ignores.append((vname, '不存在匹配的弹幕文件，跳过.'))
            continue
        tasks.append({'video': vname, 'danmaku': danmu, 'output': output})
    return tasks, ignores


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default='configs/global.yml')
    parser.add_argument('--input_dir', type=str)
    parser.add_argument('--output_dir', type=str)
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
    mode = 'transcode' if args.transcode else 'dmrender'

    input_dir = args.input_dir or config.global_config.get('downloader_args', {}).get('output_dir', '直播回放')
    inp = input(f'请输入视频文件夹路径 (回车使用默认路径 {input_dir}): \n')
    if inp:
        input_dir = inp

    # 按输入文件夹自动匹配任务配置(任务的 emoji 开关/渲染参数随之生效)
    render_args, source = resolve_render_args(config, input_dir, mode)
    assert render_args, f'请在 global.yml 中配置 render_args.{mode} 的参数'

    # 引擎选择:与主管线同规则(dmrender.emoji 开关),命令行可强制覆盖
    if mode == 'transcode':
        use_emoji = False
    elif args.emoji:
        use_emoji = True
    elif args.no_emoji:
        use_emoji = False
    else:
        use_emoji = bool(render_args.get('emoji'))

    engine_name = 'transcode' if mode == 'transcode' else ('emoji_dmrender' if use_emoji else 'dmrender')
    print(f'渲染参数来源: {source} → 渲染引擎: {engine_name}')
    print(f'即将对文件夹 {input_dir} 下的视频进行渲染')

    videos = sorted(f for f in glob(join(input_dir, '*')) if isvideo(f))
    tasks, ignores = build_tasks(videos, mode, render_args, args.output_dir)

    print('以下视频将被忽略：')
    for i, (vname, msg) in enumerate(ignores):
        print(f'[{i}] {vname}: {msg}')
    print('\n以下视频将被渲染：')
    for i, item in enumerate(tasks):
        print(f"[{i}] {item['video']} -> {item['output']}")
    print('\n如果视频被错误分类，请清理文件夹后重试')
    print('如果想选择特定的视频，请输入视频编号，例如：0 2 5')
    inp = input('或者直接回车开始全部渲染：\n')
    task_idx = [int(x) for x in inp.split()] if inp else range(len(tasks))

    if mode == 'transcode':
        from DMR.Render.transcode import Transcoder as TargetRender
    elif use_emoji:
        from DMR.Render.emoji_dmrender import EmojiDmRender as TargetRender
    else:
        from DMR.Render.dmrender import DmRender as TargetRender

    selected = [tasks[i] for i in task_idx]
    total = len(selected)
    n_ok = n_fail = 0
    for seq, task in enumerate(selected, 1):
        video = VideoInfo(
            path=task['video'],
            dm_file_id=task['danmaku'],
            duration=FFprobe.get_duration(task['video']),   # 供引擎回报进度百分比
        )
        output = task['output']
        os.makedirs(os.path.dirname(output), exist_ok=True)

        label = f'[{seq}/{total}] {os.path.basename(task["video"])}'
        print(f'{label} 开始渲染')

        def on_progress(pct, *_, _label=label):
            print(f'\r{_label} 进度 {pct}%   ', end='', flush=True)

        try:
            target_render = TargetRender(**render_args)
            status, info = target_render.render_one(video=video, output=output,
                                                    progress_cb=on_progress)
        except Exception as e:
            status, info = False, e
        print()   # 进度行换行

        if status:
            n_ok += 1
            print(f'{label} 完成 → {output}')
        else:
            n_fail += 1
            print(f'{label} 失败: {info}')

    print(f'\n全部结束：成功 {n_ok} 个，失败 {n_fail} 个。')


if __name__ == '__main__':
    try:
        main()
        input('渲染完成.')
    except Exception as e:
        logging.exception(e)
        input(f'渲染错误, {e}')
