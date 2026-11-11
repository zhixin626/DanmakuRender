# 第 10a 项：GPU 流水线剖析

状态：10a 基线、10b1–3 组合结果和 10b0 修复后的同素材 profiling 均已完成。

1. **改了什么**：`gpu_render.py` 增加环境变量开关 `DMR_PIPELINE_PROFILE=1`，记录 decode wait、copy/scale、scanner+kernel enqueue、同步等待、Encode 调用和写包阶段；profiling 开启时用 CUDA event 记录 GPU scale/blend，用 process_time 统计同步阶段 CPU 自旋；新增 `perf/pipeline_profile.py`，输出 JSON 统计。
2. **为什么这样改**：不改变生产默认路径，只在 profiling 运行中收集阶段墙钟占比，为 10b 优化顺序提供数据。
3. **遇到的问题和解决办法**：首次 CUDA event 读取使用了错误的实例方法，已改为 `cupy.cuda.get_elapsed_time`；随后确认 `NV12Buffer.load_into_canvas` 对自有 CuPy CAI 的隐式 stream 同步是主要开销，10b0 已改为直用 `.y/.uv`。
4. **意见和想法**：profiling 只在环境变量开启时写入，生产默认无计时；10b0 只改变自有 `NV12Buffer` 的导入路径，原生解码器 CAI 仍走带 stride 的包装。
5. **整体看法**：10b0 后进程 CPU 从 17.08s 降至 3.67s（约 -78.5%），墙钟基本持平；分辨率、帧数、音画时长和 0 闪烁保持不变。
6. **给 Claude 的建议**：`sync_wait` 墙钟占比仍 67.1%，按你的规则下一步做 10b4；`decode_wait + encode_call` 约 14.7%，暂不做 10b5。

脚本：`perf/pipeline_profile.py`；验证：`pytest tests -q` 159 passed；媒体 profiling 已完成。

## 10a 基线数据

素材输出 9,105 帧，统一为 182.1s / 50fps / 1080×1920。

| 版本 | wall | process CPU | copy/scale | sync wait | decode+encode |
|---|---:|---:|---:|---:|---:|
| 67e228f（10a 基线，隔离 worktree） | 17.68s | 17.08s | 11.01s（49.5%） | 1.55s（7.0%） | 2.09s（9.4%） |
| 10b1–3（51ddc0a 测量） | 17.67s | 15.58s | 12.37s（60.3%） | 1.54s（7.5%） | 2.16s（10.6%） |
| 10b0（当前） | 17.85s | 3.67s | 0.34s（1.75%） | 13.13s（67.1%） | 2.88s（14.7%） |

`CUPY_CUDA_ARRAY_INTERFACE_SYNC=0` 的验证跑为 wall 17.92s、copy/scale 0.59s（3.0%），证明隐藏 CAI 同步来源判断正确。基线与 10b0 输出均完成前 1500 帧逐帧检查：0 candidate / 0 flicker；ffprobe 两条流均 182.100s、9105 帧。
