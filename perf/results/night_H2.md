# H/G 必须修补充

1. **改了什么**：`decide_session_parts` 现在读取 `ignore_head_seconds` 和 `min_span_seconds`，LiveEvents 从 overlay 配置传入整场时间线阈值；连击 `combo_id` 与普通窗口统一对每条消息的 `gift_count` 求和；有 `tid` 时合并前去重；默认 `combo_mode` 改为 `total`，`live` 暂留状态序列但不提前显示最终值。
2. **为什么这样改**：符合 Claude 对分辨率整场过滤、B站 10.3 本条数量语义、tid 去重和“live 尚未实现就不能冒充 live”的审查。
3. **遇到的问题和解决办法**：原 H 测试把 combo_id 错当累计数，已改为两条各1显示×2；原默认 live 测试改成显式 live，生产默认 total。
4. **意见和想法**：少数方向实际 GPU 重渲染仍列为 I 后遗留；G 当前已完成记录和判定日志，短切换单测验证按整场阈值过滤。
5. **整体看法**：G/H 必须修相关 10 项测试通过；不改用户生产目录和原始 Tasks 素材。
6. **给 Claude 的建议**：请确认默认 total 的行为符合“先保证 I”的时间取舍；若明天要求 live，再单独实现 GPU 状态精灵切换。

机器：i7-14700KF / RTX 4070 Ti SUPER；实际用时约 8 分钟。
