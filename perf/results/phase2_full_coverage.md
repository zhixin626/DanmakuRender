# 阶段 2：full_coverage 覆盖验证

## 结论

阶段 2 覆盖素材已按 PR 最新审查要求重建，并使用 `Config` 的三层合并结果渲染。E 输出通过结构自检；0.5 元礼物未进入 `slide_right`，0.5 元 SC 在 `slide_left` 出现，六档 SC 在 30–35 秒内密集进入并发生队列重排。

## 输入与配置

- 视频：`perf/samples/full_coverage/峰哥_8月18日14点19分_1860-2160.flv`
- 弹幕：`perf/samples/full_coverage/峰哥_8月18日14点19分_1860-2160_augmented.jsonl`
- 原始窗口：`D:\DanmakuRender\Tasks文件\峰哥\8月18日14点19分.flv` 的 1860–2160 秒。
- 类型统计：普通弹幕 274、礼物 117、SC 12（真实 6 + 合成 6）、会员 3（真实 1 + 合成 2）、Emoji/表情 56。
- 真实 SC 价格：30、50、2、30、50、30 元；合成 SC：0.5、5、20、75、300、1200 元。
- 合成礼物价格：0.5、5、20、75、300、1200 元；0.5 元低于 `slide_right.gift.min_price_cny=1`，预期不显示。
- 测试配置：`perf/tmp/full_coverage_config/global.yml` + `DMR-峰哥.yml`；最终三层快照：`perf/tmp/full_coverage_config/resolved_DMR-峰哥.yml`。正式配置未修改；VIP 测试 UID 为 `477061263`。

## 输出与自检

- E 输出：`perf/outputs/phase1b/full_coverage/峰哥_8月18日14点19分_1860-2160/E_full_coverage.mp4`
- 演示视频：`演示视频/01_1b-1_全弹幕覆盖_左原视频右E.mp4`
- 说明：`演示视频/说明.txt`
- 源视频：1080×1920、25fps、301.718s；E：1080×1920、30fps、301.667s；时长差 0.051s；包含视频和音频流。
- 10%、50%、90% 截图：`perf/results/phase2_shots/full_coverage_p10_E.png`、`full_coverage_p50_E.png`、`full_coverage_p90_E.png`。三张 E 截图的跨帧平均绝对差分别为 31.48 和 29.77（8-bit RGB），确认不是同一帧。
- 1s/2s 源视频对照截图：对应 E 与源的平均绝对差为 2.53 和 3.47；画面主体和时间点一致。
- 目视检查：p10 有会员/礼物队列和普通弹幕，p50 有六档 SC、礼物、会员和 Emoji；未见花屏、斜切或错位。

## 运行环境

- CPU：Intel(R) Core(TM) i7-14700KF
- GPU：NVIDIA GeForce RTX 4070 Ti SUPER
- ffmpeg：`2025-12-10-git-4f947880bd-full_build-www.gyan.dev`
- 测试前 gate：仅录制、无渲染，后台 CPU 约 3–4%，已执行 `dmr_gate.py --reset` 并删除等待自动化。

## 限制

渲染日志显示 RenderPlan 布局 295 条、丢弃 111 条；丢弃主要来自超出画布或轨道容量的普通弹幕。SC/礼物/会员/VIP/Emoji 覆盖事件均在截图中出现，未把丢弃的普通弹幕误判为覆盖失败。
