# 10b3：合并每帧 metadata 上传

1. **改了什么**：SpriteCompositor 预分配/扩容 pinned host metadata `[x,y,offset,width,height,alpha]`，每帧只调用一次 cupy.asarray；blend kernel 改为 metadata 指针+stride。
2. **为什么这样改**：把原先 6 次小块 pageable 上传合并成一次，减少固定 Python/CUDA 调用开销。
3. **遇到的问题和解决办法**：无 GPU 单元环境，增加 host fallback；kernel 参数和数据顺序保持一致，已有 runtime/core 测试通过。
4. **意见和想法**：metadata 容量按 2 的幂扩容并复用，活跃事件较少时不重复申请；真正收益需短片段 profiling 验证。
5. **整体看法**：10b1–3 代码路径已完成，媒体测量仍受 DMR gate 阻塞；后续 10b4/5 依赖 profiling 结果。
6. **给 Claude 的建议**：请审查 kernel metadata 索引、pinned host fallback 和一帧一次上传保证；gate 放行后统一跑字体基准、10a profile、10b1–3 短测量。

验证：`pytest tests/test_gpu_runtime.py tests/test_render_core.py -q` 18 passed；媒体测量等待 gate。
