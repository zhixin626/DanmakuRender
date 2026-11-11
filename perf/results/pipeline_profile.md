# 第 10a 项：GPU 流水线剖析

状态：默认关闭的阶段计时器和剖析脚本已完成；≤5 分钟媒体剖析因 DMR 正在渲染/合并而等待 gate。

1. **改了什么**：`gpu_render.py` 增加环境变量开关 `DMR_PIPELINE_PROFILE=1`，记录 decode wait、copy/scale、scanner+kernel enqueue、同步等待、Encode 调用和写包阶段；profiling 开启时用 CUDA event 记录 GPU scale/blend，用 process_time 统计同步阶段 CPU 自旋；新增 `perf/pipeline_profile.py`，输出 JSON 统计。
2. **为什么这样改**：不改变生产默认路径，只在 profiling 运行中收集阶段墙钟占比，为 10b 优化顺序提供数据。
3. **遇到的问题和解决办法**：当前 DMR gate 多次报告仍有渲染/合并任务，未强行占用 GPU；B站中段完整 182 秒素材已准备到 `perf/tmp/pipeline_profile/`，门禁放行后立即运行。
4. **意见和想法**：当前计时器只在环境变量开启时写入，生产默认无计时和文件输出；下一步根据真实占比决定先做阻塞等待、元数据扫描或上传合并。
5. **整体看法**：代码改动隔离、默认行为不变；完整 CPU 回归 158 passed，短片段 profiling 和第9项 5% 性能门槛需要同一空闲窗口完成。
6. **给 Claude 的建议**：请确认阶段名称和“默认关闭”实现；gate 放行后我会补齐 `perf/results/pipeline_profile.md` 的占比表，再进入 10b 第1项。

脚本：`perf/pipeline_profile.py`；验证：`pytest tests -q` 158 passed；媒体 profiling：等待 gate。
