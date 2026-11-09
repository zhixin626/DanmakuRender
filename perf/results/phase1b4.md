# 1b-4：DMR GPU 引擎接入与回退

## 六项汇报

1. **改了什么**：新增 `DMR/Render/gpu_dmrender.py` 适配器，`DMR/Render/__init__.py` 支持 `mode/engine=gpu_render` 并在 GPU 失败时切回 `emoji_dmrender`；更新 `emoji_dmrender.py` 模块说明，明确 E 是备用路径。
2. **为什么这样改**：让现有任务配置可以选择 GPU 引擎，同时把缺少图集、GPU 依赖异常、尺寸不匹配等情况转换为可控回退，避免任务直接失败。
3. **遇到的问题和解决办法**：当前 DMR 调度层不会在生产路径自动构建 perf 图集，因此 `GpuRender` 在未提供 `sprite_data` 时主动返回失败，调度器立即调用 E；GPU 真实输出仍由 perf 脚本提供 RenderPlan/图集。
4. **意见和想法**：适配器和回退冒烟已通过；正式把 GPU 作为默认任务引擎还需要把图集构建移动到 `DMR/Render/` 公共模块，并补充任务级缓存，否则每次录播都会重新准备图集。
5. **整体看法**：1b-4 已具备安全接入边界，现有 DMR 默认行为不变；在 GPU 原型尚未覆盖分辨率画布前，回退是必要的稳定性保障。
6. **给 Claude 的建议**：请审查 `gpu_dmrender.py` 和 `Render._render_subprocess` 的回退路径；若接受适配器设计，下一步 1c 先以 perf 入口完成两小时数据，再决定是否把图集构建正式搬入 DMR。

## 验证

- `GpuRender().render_one()` 在缺少 `sprite_data` 时返回 `False, RuntimeError`，日志明确提示回退。
- `python -m py_compile DMR/Render/gpu_dmrender.py DMR/Render/__init__.py` 通过。
- 未启动 DMR 主程序，也未修改正式配置或用户数据。
