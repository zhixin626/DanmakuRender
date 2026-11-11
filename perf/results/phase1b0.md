# 阶段 1b-0：1a-2 输出自检

## 输入与输出

- 输入：perf/samples/normal_dense/normal_dense_peak_7月30日20点23分/7月30日22点50分_clip.flv
- 输出：perf/outputs/phase1a/normal_dense/normal_dense_peak_7月30日20点23分/G_gpu_proto_91.mp4
- 输入音频：AAC，48 kHz，双声道。
- 自检命令：ffprobe 检查流、分辨率、时长和帧数；在 10%、50%、90% 位置各截取一帧。

## ffprobe 结果

- 视频：HEVC，1080×1920，25 fps，7482 帧，299.194 秒。
- 音频：AAC-LC，48 kHz，双声道，299.285 秒。
- MP4：包含 2 条流，视频和音频均存在。

## 画面自检

截图位于 perf/results/phase1b0_shots/：

- G_gpu_proto_10pct.png
- G_gpu_proto_50pct.png
- G_gpu_proto_90pct.png

三张截图均为正确的竖屏 1080×1920 画布，没有 1a-2 之前的斜切扫描线或横屏宽高错误。画面中的白色横向方块是 91 精灵压力测试的预期合成内容，三处截图均正常。

阶段 1b-0 自检通过。
