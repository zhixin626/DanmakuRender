# 10b7 实验：拆分合成流与编码流

1. **A 对照**：在隔离 worktree 中创建编码器时不传 `cudastream`，其余代码保持 10b0；无采样墙钟 40.233s。
2. **B 实验**：合成使用 `compose_stream`，编码器使用独立 `encoder_stream`；blend event 由编码流 `wait_event`，FrameRing fence 记录在编码流；无采样墙钟 40.834s。
3. **NVML 采样**：进程内每秒采样会把两组绝对墙钟拉到约 41.02s，但相对可比。A/B：NVENC 19.71%/20.93%，NVDEC 11.02%/11.63%，GPU 4.78%/4.73%，显存峰值 1951/1951 MiB。
4. **输出自检**：A/B 均返回码 0，1080×1920、50fps、9105 帧、182.100s，音频同长；两份输出匹配 RenderPlan 前 1500 帧均为 0 candidate、0 flicker。
5. **结论**：B 相对 A 变慢约 1.5%，没有达到 ≥10% 采纳门槛；不合并 10b7 实验代码。当前瓶颈更接近 PyNvVideoCodec 的同步 Encode 调用，10b 优化到此停止。
