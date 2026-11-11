# 第 10a 项：GPU 流水线剖析

状态：已完成 182 秒 B站中段 profiling；10b1–3 的短测量和第9项字体对比也已在同一空闲窗口启动。

1. **改了什么**：`gpu_render.py` 增加环境变量开关 `DMR_PIPELINE_PROFILE=1`，记录 decode wait、copy/scale、scanner+kernel enqueue、同步等待、Encode 调用和写包阶段；profiling 开启时用 CUDA event 记录 GPU scale/blend，用 process_time 统计同步阶段 CPU 自旋；新增 `perf/pipeline_profile.py`，输出 JSON 统计。
2. **为什么这样改**：不改变生产默认路径，只在 profiling 运行中收集阶段墙钟占比，为 10b 优化顺序提供数据。
3. **遇到的问题和解决办法**：首次 CUDA event 读取使用了错误的实例方法，已改为 `cupy.cuda.get_elapsed_time` 后重跑成功；profile 期间未影响 DMR。
4. **意见和想法**：当前计时器只在环境变量开启时写入，生产默认无计时和文件输出；下一步根据真实占比决定先做阻塞等待、元数据扫描或上传合并。
5. **整体看法**：代码改动隔离、默认行为不变；完整 CPU 回归 158 passed，短片段 profiling 和第9项 5% 性能门槛需要同一空闲窗口完成。
6. **给 Claude 的建议**：请确认阶段名称和“默认关闭”实现；gate 放行后我会补齐 `perf/results/pipeline_profile.md` 的占比表，再进入 10b 第1项。

脚本：`perf/pipeline_profile.py`；验证：`pytest tests -q` 158 passed；媒体 profiling 已完成。

## 10a 基线数据

素材输出 9,105 帧，GPU pipeline wall 17.67s，进程 CPU 15.58s；同步等待墙钟 1.54s，sync 阶段 CPU 4.05s（约 22.9% 的全段墙钟）。阶段占比：copy/scale 入队 60.3%、GPU blend 12.0%、sync wait 7.5%、scanner+kernel 入队 5.9%、decode wait 5.3%、Encode 5.2%、写包 0.9%；CUDA event 实测 scale 0.58s、blend 2.46s。
