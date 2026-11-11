## 10b7 实验完成：不采纳拆分流

1. **改了什么**：建立两个隔离 worktree 做 A/B 实验：A 创建编码器不传 `cudastream`；B 用 `compose_stream` + `encoder_stream`，由跨流 event 保证顺序。实验改动未合并。
2. **为什么这样改**：验证 10b0 后 GPU 流串行依赖是否是低利用率和单路吞吐瓶颈。
3. **遇到的问题和解决办法**：NVML 旧接口没有 encoder 字段，已改用 `nvmlDeviceGetEncoderUtilization`/`nvmlDeviceGetDecoderUtilization`；进程内每秒采样仍会增加绝对墙钟，因此同时保留无采样结果。
4. **意见和想法**：无采样墙钟 A 40.233s、B 40.834s；NVML 采样墙钟 A 41.018s、B 41.018s。NVML 均值 A/B：NVENC 19.71%/20.93%，NVDEC 11.02%/11.63%，GPU 4.78%/4.73%，显存峰值均 1951 MiB。
5. **整体看法**：A/B 均输出 9105 帧、182.100s，前 1500 帧逐帧检查均为 0 闪烁；B 没有 ≥10% 提速，正式分支不采纳 10b7，10b 优化阶段结束。
6. **给 Claude 的建议/决策**：请确认“瓶颈在 PyNvVideoCodec 同步 Encode 调用，停止 10b 实验”的结论；下一步按要求完成第 7 项第三轮，之后进入 10c。

提交：7c52940
实际用时：约 28 分钟。

