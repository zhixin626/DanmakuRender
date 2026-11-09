# 阶段 1a：Python GPU 原型

## 环境
- CPU：Intel(R) Core(TM) i7-14700KF
- GPU：NVIDIA GeForce RTX 4070 Ti SUPER；驱动 591.86；CUDA 13.1
- PyNvVideoCodec：2.2.4（Python 3.12 Windows wheel，NVIDIA 官方索引）
- CuPy：14.2.0；CUDA 12.x runtime/NVRTC wheels
- 输入：perf/samples/normal_dense/normal_dense_peak_7月30日20点23分/，NVDEC 直接读取 FLV。
- 每项 1 次；每项前背景 CPU 采样 10 秒；进程树 CPU 和 nvidia-smi GPU/encoder/decoder 每 0.5 秒采样。

## 原型链路
PyNvVideoCodec SimpleDecoder 输出 NV12 GPU 平面；CuPy CUDA Array Interface 零拷贝包装 Y 平面；RawKernel 在显存中对每帧执行精灵压力合成。阶段 1a 的 NVENC 闭环探测失败，未生成视频输出。

## 结果
| 压力 | 帧数 | 墙钟 s | fps | 平均/峰值核数 | GPU 平均/峰值% | NVENC/解码器平均% | 返回码 |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 91 | 7482 | 11.97 | 693.1 | 0.36/1.03 | 26.7/97.0 | 0.0/67.7 | 0 |
| 50 | 7482 | 10.63 | 786.6 | 0.25/1.00 | 16.5/95.0 | 0.0/73.0 | 0 |
| 200 | 7482 | 15.01 | 525.9 | 0.45/1.03 | 45.7/98.0 | 0.0/50.0 | 0 |

## 阶段 0b 对照（normal_dense）

- 当前 E：24.24s，平均 3.47 核，84.6 核·秒。
- 普通 F：21.89s，平均 1.55 核，33.8 核·秒。

## 阻塞与结论
PyNvVideoCodec 与 CuPy 的 NVDEC/GPU kernel 路径已跑通。PyNvVideoCodec 2.2.4 Windows 编码器对 CUDA Array Interface 输入返回 Error code 8: incorrect usage of CPU input buffer；CPU 输入模式可创建编码器，但不满足阶段 1a 的全 GPU 链路。因此本阶段未生成可播放输出视频，GPU 编码接口阻塞点已保留在 raw 结果和 gpu_render.py 中。
当前原型只证明了显存解码和 CuPy 精灵压力 kernel；50/200 精灵的 CPU/GPU趋势可用于后续修复 NVENC API 后继续。按 PLAN.md 时间控制，阶段 1a 在此停止，等待下一步判断。

