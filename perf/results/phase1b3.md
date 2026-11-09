# 1b-3：整数倍输出帧率、VFR→CFR 与问题素材

## 六项汇报

1. **改了什么**：`media.resolve_output_fps` 改为输出源帧率的最小整数倍且不低于 `min_fps`；GPU 侧按源 PTS 计算每个源帧的重复次数，并在末尾补齐到源时长；新增 `04_1b-3_帧率_左30fps右50fps.mp4`。两个 `problem_*` 都尝试生成 GPU 50fps 输出。
2. **为什么这样改**：避免 25→30 的非整数重复造成滚动周期性顿挫，并让 GPU 与旧 E 使用一致的输出帧率策略；源 25fps 时按时间轴重复到 50fps。
3. **遇到的问题和解决办法**：首次流式预取导致 NVDEC/CuPy 表面失效，改为先把当前源帧复制到 GPU 缓冲再读取下一帧；末尾不足一帧导致时长差超标，增加尾部重复帧后修复。`problem_resolution` GPU 输出仍保持 1600×1280，未实现等比缩放加黑边，保留诊断输出并使用已验证的 E 回退。
4. **意见和想法**：full_coverage G 输出 15086 帧、50fps、301.694s，源 301.718s，时长差 0.024s；前 1000 帧无弹幕角落有 952 个相邻低差对，符合每个源帧大致重复两次。`problem_sc_swap` GPU 输出成功；`problem_resolution` 画布策略是当前主要遗留问题。
5. **整体看法**：整数倍 CFR 已能保持音画同步并改善低帧率滚动，GPU 仍约 30 秒完成 5 分钟素材；问题素材的分辨率切换尚未达到验收，暂时依赖 E 回退，不宣称 1b-3 全部通过。
6. **给 Claude 的建议**：请审查 `G_cfr50.mp4` 的 50fps 自检、`04_1b-3_帧率_左30fps右50fps.mp4`，以及 `problem_resolution/G_gpu_cfr50.mp4` 与 `E_fallback.mp4` 的画布差异；下一步 1b-4 接入配置时应把分辨率不匹配作为自动回退条件。

## 运行数据

| 素材 | GPU 输出 | 结果 |
|---|---|---|
| full_coverage | `perf/outputs/phase1b/full_coverage/.../G_cfr50.mp4` | 1080×1920，50fps，15086 帧，时长差 0.024s |
| problem_sc_swap | `perf/outputs/phase1b/problem_sc_swap/G_gpu_cfr50.mp4` | GPU 50fps 输出成功 |
| problem_resolution | `perf/outputs/phase1b/problem_resolution/G_gpu_cfr50.mp4` | 输出 1600×1280，需 E 回退到 `E_fallback.mp4` |

## 环境与 gate

- CPU：Intel(R) Core(TM) i7-14700KF；GPU：NVIDIA GeForce RTX 4070 Ti SUPER。
- 测试前 gate：仅录制、无渲染，后台 CPU 约 3–5%；测试结束已执行 `dmr_gate.py --reset`。
