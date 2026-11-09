# 阶段 1b-0b：固定 NV12 缓冲区复测

## 环境与隔离

- worktree：`D:\\DanmakuRender-perf`
- DMR 状态：测试期间保留 1 个直播录制 ffmpeg；未发现 DMR 渲染或合并进程，未启动或结束任何 DMR 进程。
- CPU：Intel(R) Core(TM) i7-14700KF
- GPU：NVIDIA GeForce RTX 4070 Ti SUPER，驱动 591.86
- ffmpeg：2025-12-10-git-4f947880bd-full_build-www.gyan.dev
- 输入：`perf/samples/normal_dense/normal_dense_peak_7月30日20点23分/7月30日20点23分_clip.flv`
- 压力参数：91 sprites，最多 7500 帧
- 输出：`perf/outputs/phase1a/normal_dense/normal_dense_peak_7月30日20点23分/G_gpu_proto_91.mp4`

## 本次修正

- `NV12Buffer.load` 将 PyNvVideoCodec 的 list 形式 CAI shape/strides 转换为 tuple 后交给 CuPy，修复固定 NV12 缓冲区加载第一帧时报 `Expected tuple, got list`。
- 封装时移除固定 `-t frames/25`，保留源音频完整时长；仍强制 `-map 1:a`。

## 运行结果

- 解码/合成/编码：7482 帧，26.49 秒，282.4 fps。
- `decoded_signatures`（前 200 帧每 10 帧）：前 6 个采样发生变化，随后采样值稳定；`decoded_frames_change=true`。
- 输出视频：HEVC，1080×1920，7482 帧。
- 输出音频：AAC-LC，48 kHz，双声道，1 条音频流。
- 源格式时长：300.398 秒；输出时长：300.009 秒；差值：0.389 秒（≤ 0.5 秒）。
- 截图：`perf/results/phase1b0b_shots/`，包含输出及源视频在 10%、50%、90% 的截图，并包含 1 秒、2 秒对照截图。

## 动态性与内容对照

输出截图相邻差异：

| 对比 | 平均绝对差 | 最大差 | 变化像素数 |
|---|---:|---:|---:|
| 输出 10% → 50% | 0.0337 | 5 | 161737 |
| 输出 50% → 90% | **0** | **0** | **0** |

同一时刻源视频截图：

| 对比 | 平均绝对差 | 最大差 | 变化像素数 |
|---|---:|---:|---:|
| 源 10% → 50% | 49.3234 | 251 | 6072053 |
| 源 50% → 90% | 28.8144 | 239 | 5860050 |

输出与源在 1 秒、2 秒截图的尺寸一致；平均绝对差分别为 5.3906、3.2664。人工查看 10%、50%、90% 输出截图未见斜切或花屏，但 50% 和 90% 画面完全相同。

## 结论

**1b-0b 未通过。** `decoded_frames_change=true` 说明解码器返回的前段帧确实变化；但源视频在 10/50/90% 仍明显变化，而输出 50% 与 90% 像素完全一致，说明固定 NV12 缓冲区交给 NVENC 后仍存在输出静止问题。按 PLAN.md 规定在此汇报并停止，不进入阶段 1b-1。
