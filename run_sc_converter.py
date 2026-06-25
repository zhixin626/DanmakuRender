"""
独立调用 SCConverter，将含 SC_DATA 注释行的 ASS 转换为动态动画版本。
用法:
    python run_sc_converter.py [input.ass [output.ass]]
不指定 output 则覆盖原文件（原文件移入回收站）。
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from DMR.Downloader.Danmaku.sc_converter import SCConverter

input_path = r"D:\DanmakuRender\Tasks文件\峰哥\1\6月5日23点33分.ass"
output_path = r"D:\DanmakuRender\Tasks文件\峰哥\1\6月5日23点33分_1.ass"

if __name__ == '__main__':

    count = SCConverter().convert(input_path, output_path)
    print(f'转换完成：{output_path}（共 {count} 条 SC）')
