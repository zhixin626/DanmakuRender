# 阶段 1b-1：真实弹幕 GPU 渲染

## 1. 改了什么

- 新增 DMR/Render/media.py，抽出共享的 resolve_output_fps 和 PyAV guessed_rate/average_rate 帧率探测；emoji_render_mt.py 与 gpu_render.py 共用。
- SpriteCompositor 新增 RenderPlan 图集加载、路径插值、活动事件筛选和 Y/UV CUDA kernel；新增 perf/prepare_gpu_plan.py 生成 721 个事件的 Y/U/V/alpha 图集。
- 新增 perf/bench_phase1b.py 记录 CPU/GPU；输出为 perf/outputs/phase1b/normal_dense/normal_dense_peak_7月30日20点23分/G_gpu.mp4。
- 生成五张 E/G 并排截图到 perf/results/phase1b_shots/，并生成演示视频 D:\\DanmakuRender-perf\\演示视频\\01_1b-1_真实弹幕_左旧版右GPU版.mp4。

## 2. 为什么这样改

共享 media 帧率函数修复了必须修的口径问题，避免直播 VFR 被 ffprobe avg_frame_rate 误判。图集和 kernel 复用第 (0) 步抽出的 SpriteFactory，保持生产代码不依赖 perf 虚拟环境。

## 3. 遇到的问题和解决办法

perf 的 GPU venv 没有 Pillow/PyAV，因此精灵由 DMR venv 预处理，GPU venv 只加载 atlas；media 在生产环境走 PyAV，测试环境缺 PyAV 时使用 ffprobe 兜底。GPU 输出经过 ffprobe 和 1/2 秒源帧对照，未见静止、花屏或错位。

## 4. 意见和想法

真实 RenderPlan 路径平均同屏 22.70、峰值 36，GPU kernel 同时处理 Y 和 UV，输出动态性与源一致。G 与 E 的五个时间点平均像素差为 9.77、12.34、10.61、14.74、12.90，当前阶段先以目视同步和无花屏为准，字体细节留到 1b-2。

## 5. 整体看法

GPU 运行 7500 帧耗时 15.01 秒，E 基线为 24.2 秒、F 为 21.9 秒；测量运行平均 CPU 0.288 核、峰值 0.417 核、4.74 核·秒，GPU 平均 9.52%，NVENC 46.2%，NVDEC 48.28%。速度和噪音目标目前都达标，因此没有额外加入并行线程，避免为已经达标的链路增加帧生命周期风险。

## 6. 给 Claude 的建议

建议先审查共享 media 和 SpriteCompositor 的接口；若接受当前速度和 CPU 数据，下一步进入 1b-2 做字体、描边、透明度和 PSNR 对齐。演示视频说明见 D:\\DanmakuRender-perf\\演示视频\\说明.txt，演示片段为源视频 120s 到 180s，左旧版 E、右 GPU 版。

## 环境与验证

- CPU：Intel(R) Core(TM) i7-14700KF
- GPU：NVIDIA GeForce RTX 4070 Ti SUPER，驱动 591.86
- ffmpeg：2025-12-10-git-4f947880bd-full_build-www.gyan.dev
- 输入：normal_dense/normal_dense_peak_7月30日20点23分，1080×1920@25fps
- GPU 输出：HEVC 1080×1920、7500 帧、AAC-LC 48 kHz 双声道；源/输出时长 300.398/300.009 秒，差 0.389 秒。
- 输出 10/50/90% 相邻平均差：51.82/35.05；源视频对应：49.32/28.81。
- 输出与源 1/2 秒平均差：5.04/2.89。
- 实际用时约 65 分钟，未超过预计 70 分钟。
