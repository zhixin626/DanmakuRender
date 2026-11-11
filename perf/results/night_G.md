# G：分辨率时间线与画布策略

1. **改了什么**：`DMR/Render/resolution.py` 新增 `ResolutionSpan`、稳定时间线过滤、主方向统计和整场 part 判定；`DMR/Config/default.yml` 新增开头/短片段/少数方向阈值；新增 `tests/test_resolution_policy.py`、`perf/night_resolution.py`，写入设计报告 `night_G_design.md`。
2. **为什么这样改**：段内开头不稳定和短暂方向切换不应决定画布；整场少数方向占比小则重渲染到主画布，占比大才按连续方向分 P。
3. **遇到的问题和解决办法**：problem_resolution 实材没有真实动态切换，关键帧前五帧全为1600×1280；因此真实渲染验证等比缩放/黑边，三类动态时间线由合成单测覆盖，没有伪造动态实测结果。
4. **意见和想法**：策略函数已独立、阈值可配；Merger 的生产重渲染接线仍需有真实分段时间线元数据后再接，当前阶段不把固定素材误报成动态切换。
5. **整体看法**：28 项 G/F 相关回归通过；真实 GPU 输出 1920×1080、50fps、300.2s，有音频，输出约151.6MB。演示目录含30s和150s截图，重点看黑边和画布定位。
6. **给 Claude 的建议**：请审查 `re_render_minority` 与 `parts` 阈值的边界（严格小于15%/10分钟），以及将扫描元数据挂到 VideoInfo 后接入 Merger 的最小字段集合。

机器：i7-14700KF / RTX 4070 Ti SUPER（610.88），ffmpeg 2025-12-10-git-4f947880bd；实际用时约 8 分钟（含74.4秒真实 GPU 渲染）。

设计与实测：`perf/results/night_G_design.md`；演示：`演示视频/2026-11-11_10_分辨率时间线/problem_resolution/01_problem_resolution_GPU_G.mp4`。
