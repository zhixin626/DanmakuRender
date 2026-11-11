# 10b1：阻塞式 CUDA 等待

1. **改了什么**：新增 `create_wait_event`，FrameRing 和 NVDEC 保留缓冲使用 `Event(block=True, disable_timing=True)`；旧版 CuPy 不接受 `block` 时安全回退到原构造。
2. **为什么这样改**：缓冲复用等待时让 CPU 休眠，减少自旋空转；不改变 stream 顺序和帧环逻辑。
3. **遇到的问题和解决办法**：无 GPU 测试环境依赖，使用 fake CuPy 单测确认阻塞参数；真实短片段测量待 gate 放行。
4. **意见和想法**：该项不改变 Encode 前同步，只改变事件等待策略，适合先于元数据/上传优化独立验证。
5. **整体看法**：默认生产路径只替换 wait event 创建方式，预期降低 CPU 风扇负载；性能收益需 profiling 窗口确认。
6. **给 Claude 的建议**：请审查旧 CuPy fallback；gate 放行后与 10a 基线同一素材测量 CPU、墙钟和闪烁，再决定保留。

验证：`pytest tests/test_gpu_runtime.py -q` 12 passed；媒体测量等待 gate。
