# 1b-2：文字画质对齐与配置可调验证

## 六项汇报

1. **改了什么**：新增 `perf/prepare_phase1b2.py` 生成配置 A/B 和持久化 RenderPlan，新增 `perf/compare_phase1b2.py` 计算无弹幕区域 PSNR、配置变化 MAD 和差异热力图；修复 GPU 计划的 VIP pill、面板描边/文字描边和面板入退场 alpha；用 full_coverage 生成 E/G 两套输出；演示视频为 `演示视频/02_1b-2_文字画质对齐_左E右GPU.mp4` 和 `演示视频/03_1b-2_配置可调_左配置A右配置B.mp4`。
2. **为什么这样改**：按最新评论要求，E/G 都使用源 25fps，避开 25→30 的 CFR 差异；配置 B 调整字体、字号、透明度、描边、速度和间距，验证 GPU 版是否跟随现有配置。
3. **遇到的问题和解决办法**：GPU 首帧 PTS 为 0.16 秒，比较脚本对 G 采样时间减 0.16 秒；未掩盖该偏移。校正后平均 PSNR 34.55dB，仍低于 45dB，说明 GPU 的 NV12/颜色转换、编码码率和文字描边仍需继续对齐；本阶段不把它报告为通过。
4. **意见和想法**：配置变化确实生效：E 的配置 A→B 五个采样点 MAD 为 10.05–16.24，G 为 8.62–14.78；字体、药丸字号、透明度、描边和 r2l 速度/间距均可见变化。VIP 药丸截图（`G_vip_172s.png`）和面板入场中间帧（`G_fix_20_2s.png`）确认修复生效；热力图仍显示文字/药丸边缘和底图色彩差异。
5. **整体看法**：全 GPU 链路仍保持约 15–16 秒、平均 CPU 低的性能优势，但当前文字/色彩画质验收未达标（无弹幕区域 PSNR <45dB），不能宣称 1b-2 完成。25fps 输出还会比 E 少约 0.7 秒，整数倍 CFR 和音画同步按新计划留给 1b-3。
6. **给 Claude 的建议**：请优先审查 `perf/results/phase1b2_shots/baseline_E_vs_G_heatmap.png`、`variant_E_vs_G_heatmap.png` 和两个演示视频；下一步建议先统一 GPU/E 的色彩矩阵与 NVENC 码率，再处理字体 hinting/描边，随后按 1b-3 的 25→50 CFR 规则重测。

## 对比数据

| 时间点 | E/G 无弹幕区域 PSNR | E→B MAD | G→B MAD |
|---:|---:|---:|---:|
| 30s | 35.89dB | 10.42 | 10.01 |
| 90s | 33.58dB | 16.24 | 13.97 |
| 150s | 34.66dB | 10.72 | 9.89 |
| 210s | 35.50dB | 11.47 | 8.80 |
| 270s | 33.53dB | 10.05 | 8.15 |
| **平均** | **34.63dB** | — | — |

比较输入：`E_baseline_25fps.mp4`、`G_baseline.mp4`、`E_variant_b_25fps.mp4`、`G_variant_b.mp4`；A 为 Microsoft YaHei/60/透明度 0.78/描边 1px/速度 6s，B 为 SimSun/48/透明度 0.9/描边 3px/速度 12s/垂直间距 16px。配置副本和 RenderPlan 均在 `perf/tmp/phase1b2/`。

## 运行环境与状态

- CPU：Intel(R) Core(TM) i7-14700KF；GPU：NVIDIA GeForce RTX 4070 Ti SUPER。
- E/G 均使用源 25fps；G 基线 7525 帧、约 15.12 秒，G 变体约 14.96 秒。
- 自检前 gate：仅录制、无渲染，后台 CPU 约 3–4%；测试结束后已执行 `dmr_gate.py --reset`。
- PR 评论失败排查见 `perf/results/pr_comment_issue.md`；由于 GitHub integration 403，完整汇报以本报告和提交为准。

## 面板统一实现评估

本轮先让 GPU RenderPlan 直接使用 `layout_compiler` 生成的 panel nodes，并在 `prepare_gpu_plan.py` 统一按节点绘制填充、圆角、边框和文字描边，同时让 kernel 使用入场/退场 alpha。把旧 E 的 ASS 面板也完全迁移到同一套 Pillow/SpriteFactory 实现，预计还需 1–2 小时，且会改变 E 的像素输出，必须重跑 full_coverage 的 E 基线后再比较；因此本轮没有擅自改动旧 E 的默认路径。
