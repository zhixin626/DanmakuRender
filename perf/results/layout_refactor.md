# 第 7 项：拆分 LayoutCompiler.compile

实际用时：约 20 分钟（预估 90–120 分钟；现有分支结构允许按事件类型直接抽取，三份完整计划对比一次通过）。

1. **改了什么**：将 `LayoutCompiler.compile` 的事件分支拆为 `_compile_danmaku`、`_compile_gift`、`_compile_member`、`_compile_superchat`；新增 `perf/compare_layout_refactor.py`，并在 `DMR/Config/default.yml` 的 `nrenders` 注释中记录 GPU 并发实测建议。
2. **为什么这样改**：让 `compile` 只负责公共时间线、过滤、分发、布局汇总；各事件类型的文字/药丸构造逻辑独立，便于后续维护，同时不改变行为。
3. **遇到的问题和解决办法**：第一次 source 身份断言暴露 `_dispatch_resolution_rerender` 未在测试夹具中保存 `slot["source"]`，已在第 6 项修复；本项 RenderPlan 对比未发现差异。
4. **意见和想法**：三份输入的 RenderPlan 和 next_state 均语义相等，事件计数、丢弃计数、轨道统计完全一致；拆分没有复制布局逻辑，也没有引入新依赖。
5. **整体看法**：纯重构不改变渲染速度或画质；代码边界更清晰，后续修礼物、SC 或会员时可以局部验证。`nrenders` 默认仍为 1，GPU 实测建议不超过 2。
6. **给 Claude 的建议**：请重点审查 helper 参数是否保持公共时间线语义，以及 `compare_layout_refactor.py` 的基线来源；若无必须修，第 7 项完成后按规则最多拉 3 次评论。

## 三份语义对比

| 素材 | 前后相等 | event_count | dropped_count | 轨道 accepted（r2l / SC / slide_right） |
|---|---|---:|---:|---|
| B站中段 augmented | 是 | 756 | 60 | 596 / 19 / 141 |
| `final_2h` 少年不太冷 | 是 | 985 | 35 | 885 / 36 / 64 |
| `problem_resolution` | 是 | 1162 | 1166 | 684 / 1 / 477 |

机器：Intel Core i7-14700KF / NVIDIA GeForce RTX 4070 Ti SUPER；FFmpeg `2025-12-10-git-4f947880bd`。验证：`dmr_gate.py --need-minutes 30` 放行；三份前后对比均 `equal=true`。
