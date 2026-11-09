# 第 1 步：时间轴、扫描线、帧率和统一 venv 修复

1. **改了什么**：gpu_render.py 通过 demuxer time_base 把 PTS 换算成秒并减去首帧基准，活动事件改为按 start 排序的 EventScanner；media.py 统一 PyAV guessed_rate/average_rate 和 resolve_output_fps。prepare_gpu_plan.py 逐事件读取字体、字号、粗体、颜色、透明度和描边，perf/venv 统一安装所有 GPU/媒体依赖。

2. **为什么这样改**：修复非 FLV time_base 和非零起始 PTS 的弹幕错位，避免长录播每帧遍历全部事件，并消除 GPU 版常规体写死问题。

3. **遇到的问题和解决办法**：统一 venv 初次缺 CUDA headers，补齐 nvidia-cuda-nvrtc/runtime 包并设置 CUDA_PATH 后 CuPy reduction 和 kernel 均可用。测试前运行 dmr_gate；第一次 gate 因 DMR 渲染等待，自动化唤醒后 gate 放行再完成测试。

4. **意见和想法**：新版本首帧 PTS 为 0.16 秒，末帧相对 PTS 为 300.039 秒；Compositor 按帧序号除以输出 fps 对齐旧 E，扫描线平均同屏 22.69、峰值 36。GPU 与 E 的五个时间点平均差为 9.51、11.74、10.26、14.15、12.52，字体粗体配置已生效。

5. **整体看法**：统一 venv GPU 输出 7500 帧用时 14.93 秒，平均 CPU 0.250 核、峰值 0.361 核、4.13 核·秒，GPU 8.73%，NVENC 47.08%，NVDEC 49.81%；媒体仍为 1080×1920、HEVC+AAC、时长差 0.389 秒。时间轴、长录播复杂度、字体配置和依赖环境已达到下一步条件。

6. **给 Claude 的建议**：第 2 步补充全覆盖素材时继续使用统一 perf/venv 和 dmr_gate；第 3 步再对两套字体/字号/透明度/描边配置做 PSNR 与演示视频验证。

实际执行约 50 分钟（不含 gate 等待）。
