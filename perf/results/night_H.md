# H：连续送礼合并显示

1. **改了什么**：`DMR/Render/layout_compiler.py` 新增 `merge_gift_records`，按同用户+同礼物合并，优先使用 `combo_id`，无 combo_id 时按 `combo_window_seconds`；`combo_mode=total` 合并总数，`live` 保留 `gift_states` 状态序列；默认配置写入 `live`/10 秒。新增 `tests/test_gift_combo.py`、`perf/night_combo.py`。
2. **为什么这样改**：避免同一用户连续送礼刷出一排药丸，并处理多人交替、10 秒边界和平台累计 combo 数量。
3. **遇到的问题和解决办法**：增强素材原本没有 combo 字段；已在 worktree 的 augmented 副本补 6 条 `_synthetic` 连击事件（combo_id、交替用户、超过10秒边界），不修改 Tasks 原始素材。30 秒 GPU 演示成功输出。
4. **意见和想法**：当前合并逻辑和 E/G 共用；`live` 状态序列已进入 RenderPlan 记录，但 GPU 精灵仍显示合并后的当前总数，尚未做每帧状态精灵切换；`total` 已可直接验收。
5. **整体看法**：126 项相关 CPU/结构测试（本组新增 3 项，H 组合测试全通过）；演示 `01_礼物连击_GPU_G.mp4` 30 秒、50fps，未把媒体加入 Git。
6. **给 Claude 的建议**：请审查 combo_id 累计数取 max、无 combo_id 取 sum 的平台差异；若要求 live 每次更新立即可见，下一步只需在 GPU atlas 的 gift state 上按事件时间选择精灵，不应改 E。

机器：i7-14700KF / RTX 4070 Ti SUPER（610.88）；实际用时约 10 分钟。
