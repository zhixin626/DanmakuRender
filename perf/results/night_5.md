# 第 5 项：代码收尾

实际用时：约 8 分钟（预估 20–30 分钟；文档与测试范围较小）。

1. **改了什么**：更新 `README-gpu.md`、`perf/PLAN.md`、`perf/CURRENT_TASK.md`、`perf/MERGE_CHECKLIST.md`，并在 `tests/test_gift_combo.py` 增加最低价格合并测试。
2. **为什么这样改**：把最终配置语义（5 秒滑动连击、四档价格时长、adaptive→auto）写成可执行的维护说明，避免旧设计和个人配置被误带入合并；验证低价单条在合并总额达到门槛后才显示。
3. **遇到的问题和解决办法**：未发现实现阻塞。6 条 1 元礼物合并后显示，3 条合计 3 元被过滤；测试使用同一份 LayoutCompiler 真实生成 RenderPlan。
4. **意见和想法**：文档与代码现在一致，最低价格过滤发生在 5 秒合并之后，符合“按合并后的总额过滤”；个人 `configs/` 改动仍保持未暂存。
5. **整体看法**：不影响渲染路径，代码收尾提升了配置可读性和合并安全性；当前回归测试稳定，速度和画质目标不受影响。
6. **给 Claude 的建议**：请审查旧设计标注和最低价格行为；如无必须修，下一步进入第 6 项并决定 2/3 路并行时建议的 `nrenders` 上限。

验证：`dmr_gate.py --need-minutes 10` 放行；`tests/test_gift_combo.py` 8 passed；`perf/verify_all.py --tests-only` 141 passed。
