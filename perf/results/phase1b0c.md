# 阶段 1b-0c：解码器诊断与修复

## 环境与隔离

- worktree：D:\DanmakuRender-perf
- DMR 状态：测试前只发现 DMR 主进程和直播录制 ffmpeg，没有渲染或合并进程；未启动或结束任何 DMR 进程。
- CPU：Intel(R) Core(TM) i7-14700KF
- GPU：NVIDIA GeForce RTX 4070 Ti SUPER，驱动 591.86
- ffmpeg：2025-12-10-git-4f947880bd-full_build-www.gyan.dev
- 输入：perf/samples/normal_dense/normal_dense_peak_7月30日20点23分/7月30日20点23分_clip.flv
- 诊断脚本：perf/diag_decoder.py
- 诊断上限：每种变体前 1000 帧；另用 ffmpeg 输出前 1000 帧的 Y 平面作为参考。

## 诊断结果

Y 平面校验值为全图 uint64 求和；last_change_frame 是相邻校验值最后一次变化的帧号，PTS 为解码帧提供的 timestamp/getPTS。

| 变体 | 帧数 | 唯一校验值 | 最后变化帧 | 末尾 PTS | 结论 |
|---|---:|---:|---:|---:|---|
| SimpleDecoder，device，batch=16 | 1000 | 55 | 54 | 300000 | 第 55 帧后重复 |
| SimpleDecoder，device，batch=1 | 1000 | 55 | 54 | 300000 | 第 55 帧后重复 |
| SimpleDecoder，batch=16 + deviceSynchronize | 1000 | 55 | 54 | 300000 | 无改善 |
| SimpleDecoder，scanned metadata=true | 1000 | 55 | 54 | 300000 | 无改善 |
| SimpleDecoder，host memory | 1000 | 55 | 54 | 300000 | 无改善 |
| CreateDemuxer + CreateDecoder | 1000 | 1000 | 999 | 40119 | 每帧持续变化 |
| ffmpeg Y 平面参考 | 1000 | 996 | 999 | — | 素材本身持续变化 |

5 种 SimpleDecoder 变体的首个重复帧都在约第 55 帧，PTS 随后固定为 300000；同步、batch 大小、扫描元数据和主机内存模式均不能修复。底层 demuxer/decoder 变体在 1000 帧内每帧校验值都不同，确认问题是 SimpleDecoder 在该 FLV 上的读取路径。

## 接入 GPU 原型与自检

已将 CreateDemuxer + CreateDecoder 的 packet → Decode 路径接入 DMR/Render/gpu_render.py 的 run_full，使用其返回的 NV12 GPU 帧继续固定缓冲区拷贝、CuPy 合成和 NVENC 编码。

- 91 sprites：7500 帧，15.09 秒，496.9 fps。
- 输出：perf/outputs/phase1a/normal_dense/normal_dense_peak_7月30日20点23分/G_gpu_proto_91.mp4
- ffprobe：HEVC，1080×1920，7500 帧；AAC-LC，48 kHz，双声道，1 条音频流。
- 源/输出时长：300.398 / 300.009 秒，差 0.389 秒。
- 截图：perf/results/phase1b0c_shots/
- 输出截图 10%→50% 平均绝对差 49.541，50%→90% 为 29.722；源视频对应差异为 49.323 和 28.814。三张截图人工查看无斜切、花屏或静止，50% 截图中的压力测试精灵正常出现。
- 1 秒、2 秒输出与源截图尺寸一致，平均绝对差分别为 5.404 和 3.261。

## 结论

1b-0c 已完成：SimpleDecoder 的重复帧问题已定位到其 FLV 解码路径，底层 CreateDemuxer + CreateDecoder 变体能持续返回正确变化的帧，并已接入 run_full 生成通过自检的 91 档输出。按 PLAN.md 在此停止，等待下一阶段指示。
