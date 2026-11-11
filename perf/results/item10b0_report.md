## 10b0 完成：消除 NV12Buffer 的隐式 CAI stream 同步

1. **改了什么**：`NV12Buffer.load_into_canvas` 遇到自有 `NV12Buffer` 时直接使用 `.y/.uv`，保留 `ready` event 等待；原生解码器输出仍走 `CaiView` stride 包装。补了直通路径回归测试。
2. **为什么这样改**：自有 CuPy CAI 含 `stream=1`，重新 `cupy.asarray(CaiView(...))` 会在默认设置下同步整条 stream，造成每帧隐藏自旋。
3. **遇到的问题和解决办法**：第一次基线 profiling 暴露 67e228f 的历史 `Event.elapsed_time` 读取错误；只在隔离基线 worktree 临时改成 `cupy.cuda.get_elapsed_time` 后完成测量，当前分支没有带入该临时改动。
4. **意见和想法**：同素材 9105 帧：67e228f 基线 wall 17.68s / process CPU 17.08s；10b1–3 wall 17.67s / CPU 15.58s；10b0 wall 17.85s / CPU 3.67s。copy/scale 从 11.01s 降至 0.34s；`CUPY_CUDA_ARRAY_INTERFACE_SYNC=0` 对照为 0.59s，验证根因。
5. **整体看法**：10b0 完成；基线和修复后输出均 1080×1920、50fps、9105 帧、182.100s，音频同长，匹配 RenderPlan 前 1500 帧逐帧检查均为 0 candidate / 0 flicker。完整回归 `160 passed`。
6. **给 Claude 的建议/决策**：`sync_wait` 仍占 67.1% 墙钟，超过 15%，按规则下一步进入 10b4；`decode_wait + encode_call` 约 14.7%，暂不做 10b5。请审查后给出 10b4 的采纳决定。

提交：41a9f50
实际用时：约 36 分钟（含三次隔离基线重跑）。
