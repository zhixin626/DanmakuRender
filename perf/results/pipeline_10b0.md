# 10b0：消除 NV12Buffer 的隐式 CAI stream 同步

1. **改动**：`NV12Buffer.load_into_canvas` 遇到自有 `NV12Buffer` 时直接使用 `.y`、`.uv`，保留 `ready` event 等待；只有原生解码器输出继续走 `CaiView` stride 包装。
2. **验证**：自有 CuPy CAI 的 key 包含 `stream=1`。同素材 9105 帧 profiling：基线 67e228f wall 17.68s / CPU 17.08s；10b1–3 wall 17.67s / CPU 15.58s；10b0 wall 17.85s / CPU 3.67s。copy/scale 从 11.01s 降至 0.34s。
3. **输出自检**：10b0 视频 1080×1920、50fps、9105 帧、182.100s，音频同长；匹配 RenderPlan 前 1500 帧逐帧检查 0 candidate、0 flicker。
4. **门槛判断**：`sync_wait` 仍占 67.1% 墙钟，超过 15%，因此按规则进入 10b4；`decode_wait + encode_call` 约 14.7%，不进入 10b5。
