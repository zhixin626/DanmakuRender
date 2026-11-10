# 1d-0c：面板形状、多行 SC 与 v2 演示修复

## 六项汇报

1. **改了什么**：`panel_sprite` 按 `round_top/round_bottom` 绘制对应圆角并保留描边；面板文字按换行逐行调用 `SpriteFactory.get_sprite_image`；配置 B 同时修改 `slide_left/slide_right` 的字体、颜色、圆角和字号；修正 CFR `max_frames` 尾部补帧绕过；本次 v2 视频归档到 `演示视频/2026-11-10_02_0c_面板与闪烁修复/`。
2. **为什么这样改**：对齐旧版上下相接的单个药丸形状，修复 SC 长内容只显示第一行，并让配置可调验证覆盖面板样式；避免 04 对比视频因帧数限制导致一侧停在最后一帧。
3. **遇到的问题和解决办法**：旧 panel_sprite 忽略节点 shape，且 `get_sprite_image` 只处理单行；现在按 shape 处理填充、按行绘制，使用 SpriteFactory 的缩放、Emoji、描边和颜色。
4. **意见和想法**：0c 截图 `G_0c_30s.png`、`G_0c_32s.png` 显示上下圆角和 SC 两行内容；`check_flicker.py` 31 个时刻检查结果仍为 0 疑似漏叠加帧。
5. **整体看法**：0a/0b/0c 的用户可见问题已完成修复和验证，v2 交付视频已按子文件夹归档；码率控制仍是下一项最优先任务。
6. **给 Claude 的建议**：请审查 `perf/results/phase1d_0c_shots/` 和 `演示视频/2026-11-10_02_0c_面板与闪烁修复/`；确认后继续 1d-1 的 CreateEncoder 码率控制。

## 验证

- `G_0c_25fps.mp4`：1500 帧、25fps、约 60 秒。
- `check_flicker.py`：`checked=31, flicker_frames=0`。
- v2 视频：02、03、04 已归档到本次交付子文件夹。
