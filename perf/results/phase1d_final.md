# 1d 最终补充验收

## A：码率根因修复

- **完成。** `CreateEncoder` 使用 PyNvVideoCodec 的正确 `fps` 参数，删除 `reference_fps` 和归一化函数；日志记录 codec、rc、bitrate、maxbitrate、vbvbufsize、preset、fps、宽高。
- 原因：旧代码传入 `framerate`，驱动静默采用默认 30fps，造成 25fps 约 3.4Mbps、60fps 约 8.2Mbps。
- 同一素材、同一配置（4M / 6M / 8M）复测：25fps **4,134,688 bit/s**，60fps **4,024,653 bit/s**，均在配置值 ±10% 内。

## B：20 分钟最高密度遥测

事件密度统计后选择 2 小时录播第 3600–4800 秒窗口（217 条原始事件，6 个 20 分钟窗口中最高）。

| 后端 | 墙钟 | 平均核数 | 峰值核数 | 核·秒 | GPU 平均 | NVENC 平均 | NVDEC 平均 |
|---|---:|---:|---:|---:|---:|---:|---:|
| E | 201.7s | 3.325 | 5.159 | 631.8 | 21.37% | 35.79% | 37.88% |
| G | 147.6s | 0.931 | 1.359 | 130.3 | 8.62% | 47.86% | 50.07% |

G 比 E 快 26.9%，平均核数降低 72.0%，核·秒降低 79.4%。两边均为 1920×1080、60fps、约 1200s；E 72002 帧/1200.033s，G 72000 帧/1199.966s。原始遥测在 `perf/tmp/telemetry_20m_dense_results/telemetry.json`。

## C：第 2 步重构与第 3 步 1d

### C2 重构

- **完成：** 图集正式代码位于 `DMR/Render/gpu_atlas.py`，`gpu_dmrender.py` 自动生成临时图集并清理；图集根目录和面板节点读取问题已修正。
- **完成：** `scheduled_frames()` 统一直通、VFR、整数倍补帧和结尾时间轴；`gpu_render.py` 只保留一个 `Encode` 循环。
- **完成：** 新增 `RenderEvent`/`PanelNode` dataclass，缺少固定字段直接抛 `KeyError`；面板形状按上下角分别圆角，文字复用 `SpriteFactory`，多行按行高绘制。
- **完成：** pytest **7 passed**；覆盖帧调度、VFR、扫描线进入/退出/顺序、固定结构缺字段、码率解析、帧率和 `fps` 参数防回归；`verify_all.py` 通过。

### C3 1d 剩余

- **面板抗锯齿：完成。** 现有 `phase1d_0c_shots` 的放大截图和面板节点测试确认上下药丸直线相接、外轮廓统一描边；Pillow 圆角按四角掩码生成。
- **04_G_50fps 左侧 SC：完成。** 根因是并排视频时长不一致，短侧结束后 `hstack` 保留最后一帧，并非帧调度丢 SC；独立 30fps/50fps 文件均为同一时段约 30s，保留原有逐帧闪烁结果 0。
- **1d-2 画质：完成。** 用同一源 FLV、相同帧号，在 30/90/150s 选择 E/G 同时无弹幕的 128×128 区域：E 对源平均 **49.89dB**，G 对源平均 **50.24dB**，G−E **+0.35dB**；Y/U/V 均值最大绝对差 **0.19**，满足 G ≥ E−0.5dB 和均值差 <1。
- **1d-3 CPU/GPU：完成。** 见本报告 B 表。
- **1d-5 分辨率：完成。** `NV12Buffer.load_scaled()` 在 GPU 上按等比缩放、偶数尺寸和黑边填充；`problem_resolution` 60s/1500 帧输出成功，1600×1280、25fps、码率 4,176,783 bit/s，未回退到 E。当前样本的编码流实际尺寸未在中途改变，因此动态尺寸分支以 GPU 缩放单元路径和 smoke 输出验证。

## B站平台表情缓存

- **完成。** 递归扫描 `D:\DanmakuRender\Tasks文件\峰哥` 的 5 个原始 JSONL，收集 96 个嵌套 `emoticon.emoticon_unique`（含 `official_*`、`upower_*`）。
- 从 `emoji_pack/bilibili/emoticon` 复制 93 个已有缓存，并用共享 `EmoticonStore` 按原始记录 URL 下载补齐 3 个 URL-only 条目；目标目录最终 96 个注册项、96 张图片。同步图集生成成功，295 个 full_coverage 事件、5,314,052 atlas bytes。
- 细节见 `perf/results/emoticon_cache_sync.md`。

## 演示产物

本轮演示目录：`演示视频/2026-11-10_03_码率与1d验收/`，包含 25/60fps 码率复测、problem_resolution GPU 输出、PSNR/YUV 说明和缓存同步说明。

## 遗留与限制

- `problem_resolution` 当前公开样本本身没有可由 ffprobe 观察到的中途编码尺寸切换；GPU 动态尺寸加载和缩放路径已实现，但还需要真正带尺寸切换的录播流做最终目视验收。
- 20 分钟遥测使用最高事件密度窗口；E/G 输出保存在本地 `perf/tmp`，未把大视频提交到 Git。
