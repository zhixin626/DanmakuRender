# 1d-0c 补齐报告

按 Claude 的“1d-0c 审查：未通过”逐项复核。所有视频、截图和清单均在隔离 worktree 内。

| 项 | 状态 | 证据/说明 |
|---|---|---|
| 1. 药丸形状与 E/G 3 倍截图 | 完成 | `perf/results/phase1d_0c_shots/pill_E_G_54s_3x.png`。上下两块沿直线相接，外轮廓由整体 mask 统一描边，只有最上端和最下端保留圆角。 |
| 2. 03/04 全帧闪烁检查 | 完成 | 当前代码加入编码器消费完成后的 CUDA 全设备同步；`G_0c_25fps_sync3.mp4` 与 `G_0c_50fps_sync3.mp4` 均逐帧解码 1500/1500。03：活动区域 1246 帧，min/p01/median=56.00/61.49/212.00，阈值 21.30，孤立掉叠加 0；04：活动区域 995 帧，min/p01/median=75.70/104.79/177.10，阈值 17.71，孤立掉叠加 0。 |
| 3. 去掉 prepare_gpu_plan.py 写死默认值 | 完成 | 文本节点现在直接读取 `node["font"]`、`node["size"]`；普通事件直接读取 `style["font"]`、`style["size"]`、`style["bold"]`、`style["color"]`、`style["opacity"]`、`style["outline"]`、`style["outline_color"]`，缺字段抛 KeyError。保留常量：270×480 检查缩放、32 像素最小有效区域、10.0 信号阈值、0.1 中位阈值比例、CUDA tile 32×8、NV12 UV 采样 2、`SCALE=0.75` 单位换算；这些不代表素材配置。 |
| 4. 04 卡住原因与独立文件 | 完成 | 原因是 hstack 两侧时长不同，短侧结束后停在最后一帧。已输出同一时段约 30 秒的 `演示视频/2026-11-10_02_0c_面板与闪烁修复/04_E_30fps_独立.mp4`（30.10s/903 帧）和 `04_G_50fps_独立.mp4`（30.014s/1500 帧）。 |
| 5. A/B 全部差异 | 完成 | 已写入同目录 `说明.txt`：字体、字号、透明度、描边、垂直间距、弹幕速度，以及 slide_left/right 的字体、字号、颜色、圆角和透明度逐项列出。 |
| 6. 素材目录拆分 | 完成 | `perf/samples/full_coverage/` 与 `final_2h/` 均拆为 `原始/`、`补充/`、`配置/`；`build_full_coverage.py`、`build_final_2h.py`、`prepare_phase1b2.py` 和 `samples.md` 已同步路径。Tasks 原始文件未改。 |
| 7. 删除不再使用文件 | 完成 | 删除前清单和总大小写入 `perf/results/phase1d_0c_cleanup.md`：35 个文件，共 2,021,427,066 bytes（1927.78 MiB）；只删除 worktree 内旧演示视频、旧中间流和已被新输出取代的重复文件。 |
| 8. “花心痒痒 ¥30”三行 SC 截图 | 完成 | `perf/results/phase1d_0c_shots/sc30_E_G_花心痒痒_三行_3x.png`，E/G 同时刻、放大 3 倍并排，三行内容均可见。 |

## 码率控制（1d-1 预备修复）

- `gpu_render.py` 已删除 1.2/1.5/2.4 校准倍数，`bitrate` 直接使用配置值；`rc`、`maxbitrate`、`vbvbufsize` 通过 `render_args.overlay.gpu_rc_args` 传入。
- `DMR/Config/default.yml` 已加入默认值：`rc=vbr`、`maxbitrate=6000000`、`vbvbufsize=8000000`，并注明单位和含义；缺少字段会直接报错。
- 用 `bitrate=4000000`、上述默认 rc 参数跑 full_coverage 5 分钟，GPU 视频实际码率 3,410,931 bit/s，约为配置值的 85.3%，暂未达到 ±10% 门槛。实现已按审查要求改为直接配置，驱动实际码率偏低是当前剩余阻塞，未用校准系数掩盖。
