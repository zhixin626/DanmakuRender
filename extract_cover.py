# -*- coding: utf-8 -*-
"""
交互式封面提取工具
用法: python extract_cover.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from DMR.Uploader.acfun import extract_best_frame, _HAS_CV2


def parse_ratio(s: str) -> float:
    s = s.strip()
    if not s:
        return 1.6
    if '/' in s:
        parts = s.split('/', 1)
        return float(parts[0]) / float(parts[1])
    return float(s)


def main():
    if not _HAS_CV2:
        print('错误: 未安装 opencv-python，请先运行: pip install opencv-python')
        sys.exit(1)

    print('=== 封面提取工具 ===\n')

    while True:
        video_path = input('请输入视频路径: ').strip().strip('"')
        if os.path.isfile(video_path):
            break
        print(f'文件不存在: {video_path}，请重新输入\n')

    ratio_input = input('裁剪比例 width/height（默认 1.6，支持如 16/9 或 1.78）: ').strip()
    try:
        ratio = parse_ratio(ratio_input) if ratio_input else 1.6
        if ratio <= 0:
            raise ValueError('比例必须大于0')
    except Exception as e:
        print(f'比例输入有误 ({e})，使用默认值 1.6')
        ratio = 1.6

    output_dir = os.path.dirname(os.path.abspath(video_path))

    print(f'\n正在提取封面（比例={ratio:.4f}）...')
    try:
        result_path = extract_best_frame(video_path, output_dir=output_dir, ratio=ratio)
        print(f'\n封面已保存至: {result_path}')
    except Exception as e:
        print(f'提取失败: {e}')
        sys.exit(1)


if __name__ == '__main__':
    main()
