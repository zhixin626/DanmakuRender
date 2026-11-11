# 第7项：render_only交互重设计

状态：完成。用时约16分钟（含等待正式DMR放行）。新增15项交互测试，通过全部83项CPU回归（最终数量后续步骤会增加）。

1. 改了什么：先选源视频及弹幕，再选任务与输出，最后汇总确认。列出全部弹幕候选、augmented优先；任务按目录匹配默认但需确认；已有文件可覆盖/改名/跳过，覆盖失败保留旧文件。
2. 为什么：消除旧流程选择顺序混乱和静默忽略；视频列表过滤已渲染名称和同名FLV的MP4。
3. 问题与解决：SDK官方LOGGER_LEVEL=ERROR在导入前设置，保留用户显式调试值和错误日志；真实运行已无FLV seek提示。正式DMR忙时先做CPU交互测试，门禁放行后再真实渲染。
4. 意见：命令行模式保持不提问；真实配置和BAT改动保留。
5. 整体：两条实际路径均完成1个182秒GPU渲染，未启动main.py/录制/上传；测试使用真实素材副本及隔离配置。
6. 给Claude：优先审查默认任务目录匹配和覆盖时的原文件保护，下一步按序处理绘制问题。

## 单文件完整实际记录

```text
输入（按提示顺序）：
1: D:\DanmakuRender-perf\perf\tmp\interactive\素材\case.flv
2: <回车>
3: <回车>
4: <回车>
5: <回车>
6: 2
7: single.mp4
8: <回车>

实际stdout：
请输入视频文件或文件夹（可拖入）：找到以下弹幕文件（D:\DanmakuRender-perf\perf\tmp\interactive\素材）：
  [1] case_augmented.jsonl ← 默认
  [2] case.jsonl
弹幕编号或其他路径（回车选1）：任务配置：
  [0] 全局配置
  [1] 峰哥 ← 默认
任务编号（回车选1）：输出文件夹（回车：D:\DanmakuRender-perf\perf\tmp\interactive\素材）：输出文件名（回车：case（弹幕版）.mp4）：输出已存在：D:\DanmakuRender-perf\perf\tmp\interactive\素材\case（弹幕版）.mp4
[1] 覆盖  [2] 改名  [3] 跳过：输出文件名（回车：case（弹幕版）.mp4）：──────── 确认 ────────
任务：任务「峰哥」（引擎：GPU 弹幕渲染）
视频 | 弹幕 | 输出
D:\DanmakuRender-perf\perf\tmp\interactive\素材\case.flv | D:\DanmakuRender-perf\perf\tmp\interactive\素材\case_augmented.jsonl | D:\DanmakuRender-perf\perf\tmp\interactive\素材\single.mp4
回车开始，输入n取消：渲染参数来源: 任务「峰哥」 → 渲染引擎: gpu_render
[1/1] case.flv 开始渲染

[1/1] case.flv 进度 0.0%
[1/1] case.flv 进度 2.7%
[1/1] case.flv 进度 5.4%
[1/1] case.flv 进度 8.2%
[1/1] case.flv 进度 10.9%
[1/1] case.flv 进度 13.7%
[1/1] case.flv 进度 16.4%
[1/1] case.flv 进度 19.2%
[1/1] case.flv 进度 22.0%
[1/1] case.flv 进度 24.7%
[1/1] case.flv 进度 27.5%
[1/1] case.flv 进度 30.2%
[1/1] case.flv 进度 33.0%
[1/1] case.flv 进度 35.7%
[1/1] case.flv 进度 38.5%
[1/1] case.flv 进度 41.2%
[1/1] case.flv 进度 44.0%
[1/1] case.flv 进度 46.8%
[1/1] case.flv 进度 49.5%
[1/1] case.flv 进度 52.3%
[1/1] case.flv 进度 55.0%
[1/1] case.flv 进度 57.8%
[1/1] case.flv 进度 60.5%
[1/1] case.flv 进度 63.3%
[1/1] case.flv 进度 66.0%
[1/1] case.flv 进度 68.8%
[1/1] case.flv 进度 71.5%
[1/1] case.flv 进度 74.3%
[1/1] case.flv 进度 77.0%
[1/1] case.flv 进度 79.8%
[1/1] case.flv 进度 82.6%
[1/1] case.flv 进度 85.3%
[1/1] case.flv 进度 88.1%
[1/1] case.flv 进度 90.8%
[1/1] case.flv 进度 93.6%
[1/1] case.flv 进度 96.3%
[1/1] case.flv 进度 99.0%
[1/1] case.flv 进度 100.0%
[1/1] case.flv 完成 → D:\DanmakuRender-perf\perf\tmp\interactive\素材\single.mp4

全部结束：成功 1 个，失败 0 个。

实际stderr：

退出码：0

```

## 文件夹完整实际记录

```text
输入（按提示顺序）：
1: D:\DanmakuRender-perf\perf\tmp\interactive\素材
2: 1 2
3: <回车>
4: <回车>
5: <回车>
6: <回车>
7: <回车>
8: 2
9: folder.mp4
10: <回车>

实际stdout：
请输入视频文件或文件夹（可拖入）：该文件夹中的源视频（已排除渲染输出）：
  [1] case.flv    弹幕：✓ case_augmented.jsonl
  [2] missing.flv    弹幕：✗ 未找到
  [3] single.mp4    弹幕：✗ 未找到
视频编号（空格分隔，回车选所有有弹幕的，n取消）：找到以下弹幕文件（D:\DanmakuRender-perf\perf\tmp\interactive\素材）：
  [1] case_augmented.jsonl ← 默认
  [2] case.jsonl
弹幕编号或其他路径（回车选1，0跳过）：missing.flv 未找到弹幕，请输入路径（回车跳过）：任务配置：
  [0] 全局配置
  [1] 峰哥 ← 默认
任务编号（回车选1）：输出文件夹（回车：D:\DanmakuRender-perf\perf\tmp\interactive\素材）：输出文件名（回车：case（弹幕版）.mp4）：输出已存在：D:\DanmakuRender-perf\perf\tmp\interactive\素材\case（弹幕版）.mp4
[1] 覆盖  [2] 改名  [3] 跳过：输出文件名（回车：case（弹幕版）.mp4）：──────── 确认 ────────
任务：任务「峰哥」（引擎：GPU 弹幕渲染）
视频 | 弹幕 | 输出
D:\DanmakuRender-perf\perf\tmp\interactive\素材\case.flv | D:\DanmakuRender-perf\perf\tmp\interactive\素材\case_augmented.jsonl | D:\DanmakuRender-perf\perf\tmp\interactive\素材\folder.mp4
回车开始，输入n取消：渲染参数来源: 任务「峰哥」 → 渲染引擎: gpu_render
[1/1] case.flv 开始渲染

[1/1] case.flv 进度 0.0%
[1/1] case.flv 进度 2.6%
[1/1] case.flv 进度 5.4%
[1/1] case.flv 进度 8.1%
[1/1] case.flv 进度 10.9%
[1/1] case.flv 进度 13.6%
[1/1] case.flv 进度 16.4%
[1/1] case.flv 进度 19.1%
[1/1] case.flv 进度 21.9%
[1/1] case.flv 进度 24.6%
[1/1] case.flv 进度 27.3%
[1/1] case.flv 进度 30.1%
[1/1] case.flv 进度 32.8%
[1/1] case.flv 进度 35.6%
[1/1] case.flv 进度 38.3%
[1/1] case.flv 进度 41.0%
[1/1] case.flv 进度 43.8%
[1/1] case.flv 进度 46.5%
[1/1] case.flv 进度 49.2%
[1/1] case.flv 进度 52.0%
[1/1] case.flv 进度 54.7%
[1/1] case.flv 进度 57.5%
[1/1] case.flv 进度 60.2%
[1/1] case.flv 进度 63.0%
[1/1] case.flv 进度 65.7%
[1/1] case.flv 进度 68.4%
[1/1] case.flv 进度 71.2%
[1/1] case.flv 进度 73.9%
[1/1] case.flv 进度 76.7%
[1/1] case.flv 进度 79.4%
[1/1] case.flv 进度 82.2%
[1/1] case.flv 进度 84.9%
[1/1] case.flv 进度 87.7%
[1/1] case.flv 进度 90.4%
[1/1] case.flv 进度 93.2%
[1/1] case.flv 进度 95.9%
[1/1] case.flv 进度 98.7%
[1/1] case.flv 进度 100.0%
[1/1] case.flv 完成 → D:\DanmakuRender-perf\perf\tmp\interactive\素材\folder.mp4

全部结束：成功 1 个，失败 0 个。

实际stderr：

退出码：0

```
