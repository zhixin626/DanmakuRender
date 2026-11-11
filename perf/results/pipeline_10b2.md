# 10b2：增量 EventScanner

1. **改了什么**：EventScanner 维护按 z_order/index 的 active_order，新增事件用有序插入；过期事件增量移除；每个事件缓存 path segment index，时间单调前进时 O(1) 插值，不再每帧从路径首点线性扫描和重新排序。
2. **为什么这样改**：减少每帧 Python 排序和路径查找，降低单线程 CPU 瓶颈，同时保持原有 z 顺序、淡入淡出和事件过期语义。
3. **遇到的问题和解决办法**：补充多段路径和 active_order 断言；过期事件同时清理 path_indices，并删除已无调用方的 event_position。媒体性能和闪烁测量待 gate。
4. **意见和想法**：当前仍返回同样的 visible 元组，kernel 接口不变，属于可回退的纯扫描优化。
5. **整体看法**：预期降低 scanner 阶段 CPU 占比；需要与 10a profile 对比后决定是否保留。
6. **给 Claude 的建议**：请审查 active_order 插入/过期边界和 path index 单调假设；下一步进入10b3合并上传。

验证：`pytest tests/test_render_core.py -q` 7 passed；媒体测量等待 gate。
