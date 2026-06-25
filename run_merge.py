"""视频合并的无头运行脚本（供字幕编辑器以子进程方式调用）。

用法：
    python run_merge.py <任务json路径>

任务 json 格式：
    {
        "inputs":   ["D:/a.mp4", "D:/b.flv", ...],   # 按合并顺序排列
        "output":   "D:/合并.mp4",                    # 输出文件路径
        "hw_encode": true                            # 是否启用 NVENC 硬件编码（默认 true）
    }

成功时最后一行打印 "MERGE_DONE\t<输出路径>"，退出码 0；失败退出码非 0。
合并逻辑（分辨率/时间基归一、按需重编码、NVENC、时间戳修复）全部复用 DMR.utils.merge_mp4。
"""
import json
import sys

from DMR.utils.merge_mp4 import merge_mp4


def main():
    if len(sys.argv) < 2:
        print("用法: python run_merge.py <任务json路径>")
        sys.exit(2)

    with open(sys.argv[1], "r", encoding="utf-8") as f:
        task = json.load(f)

    inputs = [p for p in task.get("inputs", []) if p]
    output = task.get("output")
    hw_encode = bool(task.get("hw_encode", True))

    if len(inputs) < 2:
        print("至少需要 2 个视频才能合并")
        sys.exit(2)
    if not output:
        print("未指定输出路径")
        sys.exit(2)

    print(f"开始合并 {len(inputs)} 个视频 -> {output}（硬件编码={hw_encode}）")
    for i, p in enumerate(inputs, 1):
        print(f"  {i}. {p}")

    # remover=False：合并是把源文件首尾相接，绝不删除用户的原始视频
    result = merge_mp4(inputs, remover=False, hw_encode=hw_encode, out_path=output)
    print(f"MERGE_DONE\t{result}")


if __name__ == "__main__":
    main()
