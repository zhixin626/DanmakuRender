# 阶段 1a：Python GPU 原型与 1a-2 NVENC 闭环

## 环境

- CPU：Intel(R) Core(TM) i7-14700KF
- GPU：NVIDIA GeForce RTX 4070 Ti SUPER；驱动 591.86；CUDA 13.1
- PyNvVideoCodec：2.2.4（Python 3.12 Windows wheel，NVIDIA 官方索引）
- CuPy：14.2.0；CUDA 12.x runtime/NVRTC wheels
- 输入：`perf/samples/normal_dense/normal_dense_peak_7月30日20点23分/`，NVDEC 直接读取 FLV。
- 每项 1 次；每项前背景 CPU 采样 10 秒；进程树 CPU 和 nvidia-smi GPU/encoder/decoder 每 0.5 秒采样。

## 原型链路

PyNvVideoCodec SimpleDecoder 输出 NV12 GPU 平面；CuPy CUDA Array Interface 零拷贝包装 Y 平面；RawKernel 在显存中执行精灵压力合成。1a-2 增加了 `cuda()` 包装类，返回形状为 `(H,W,1)` 和 `(H/2,W/2,2)`、按 pitch 设置的两个 NV12 平面，并将 HEVC 码流封装为带源音频的 MP4。

## 1a GPU kernel 压力测试

| 压力 | 帧数 | 墙钟 s | fps | 平均/峰值核数 | GPU 平均/峰值% | NVENC/解码器平均% | 返回码 |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 91 | 7482 | 11.97 | 693.1 | 0.36/1.03 | 26.7/97.0 | 0.0/67.7 | 0 |
| 50 | 7482 | 10.63 | 786.6 | 0.25/1.00 | 16.5/95.0 | 0.0/73.0 | 0 |
| 200 | 7482 | 15.01 | 525.9 | 0.45/1.03 | 45.7/98.0 | 0.0/50.0 | 0 |

## 1a-2 完整链路：GPU NVENC + MP4 封装

| 精灵 | 帧数 | 墙钟 s | fps | 平均/峰值核数 | GPU 平均/峰值% | NVENC/解码器平均% | 返回码 | 输出 |
|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 91 | 7480 | 24.21 | 308.9 | 0.11/0.25 | 14.2/21.0 | 32.1/29.7 | 0 | `perf/outputs/phase1a/normal_dense/normal_dense_peak_7月30日20点23分/G_gpu_proto_91.mp4` |
| 200 | 7480 | 24.66 | 303.3 | 0.12/0.32 | 25.9/43.0 | 28.4/33.8 | 0 | `perf/outputs/phase1a/normal_dense/normal_dense_peak_7月30日20点23分/G_gpu_proto_200.mp4` |

## 阶段 0b 对照（normal_dense）

- 当前 E：24.24s，平均 3.47 核，84.6 核·秒。
- 普通 F：21.89s，平均 1.55 核，33.8 核·秒。

1a-2 已打通 NVENC 并产出可播放 MP4。按 PLAN.md 完成后停止，等待下一阶段指示。
