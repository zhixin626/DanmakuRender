# 合并准备（只做清单，不实际合并）

目标分支：`zhinxinchange_v5`。以下是最终内容选择清单，不能把性能分支整棵目录覆盖正式 DMR。

## 包含

- 本分支实际修改的 `DMR/` 运行代码：Config 三层配置与注释；Render 引擎登记/BaseRender/E/G/sprites/layout/resolution；EmojiPack/common 缓存；Task/liveevents；utils/ffprobe、utils 公共工具。G 若修改 Merger，也包含实际修改的 Merger 文件。
- `render_only.py`、`requirements.txt`、`requirements-gpu.txt`、`README-gpu.md`。
- `tests/` 中产品回归及 `tests/references/`、`tests/render_checks.py`、`tests/__init__.py`。**例外：不带 `tests/test_gate.py`**，它只验证 `perf/dmr_gate.py`，不是产品测试；保留在性能分支。
- `DMR/Config/default.yml` 的新配置和中文说明；`.gitignore` 中必要的 venv/临时产物忽略规则。用户的 global/task YAML 另行人工按需改，不整份覆盖。

参考当前实际差异的只读命令：

```powershell
$mergeBase = git merge-base perf/emoji-render origin/zhinxinchange_v5
git diff --name-status $mergeBase perf/emoji-render -- DMR tests render_only.py requirements.txt requirements-gpu.txt README-gpu.md .gitignore
```

I 完成后再用最终 HEAD 核对一次，不能漏掉 G/H 的实际落地改动或它们的测试。

## 不包含

- 整个 `perf/`、`演示视频/`、`Tasks文件/`、录像/原始与增强 JSONL、性能截图、遥测结果和开发用脚本。
- 开发 venv、CUDA 缓存、`__pycache__`、`.temp/`、logs、`.dmr/` 恢复记录。
- `DMR-月亮3.bat`、`configs/DMR-月亮3.yml`、本机 `configs/global.yml` 及其他个人任务配置。
- `.login_info/`、cookies、账号凭据、个人封面与平台 emoticon 下载缓存。正式环境已有表情可保留，或通过原有入口下载。
- 任何工作树的绝对路径/环境私货。产品代码不得 import perf，测试中长期使用的闪烁助手现已移入 tests。

## 合并前需要确认

- 用户已通过新A2决定采用简单方案：移除LiveEvents检查点，停止任务进入已有failed_tasks，独立重试成功后不自动恢复已释放组的合并。**A2修复及回归完成前不做正式合并**，以A2和最终汇报确认删除范围。
- B 的极窄“词＋禁行首标点”会整行等比适配，可能出现局部字号变化；已记录为建议，等待用户目视决定。
- G/H 实际完成程度、最终两小时/显存/闪烁结果和遗留问题以 `perf/results/验收总结.md` 为准。

## 合并后正式环境步骤（由维护者执行）

1. 等所有录制、渲染、合并和上传空闲，备份个人配置及恢复记录，再停止对应实例。
2. 选择包含路径审核差异、处理冲突；不导入任何开发配置、凭据或视频。
3. 在正式实例自己的 venv 中安装 `requirements-gpu.txt`，检查 `nvidia-smi`、FFmpeg、字体和平台表情目录。
4. 产品测试执行 `python -m pytest tests -q`；前提是按上面规则未带入门禁工具专用测试。核心停止恢复、Render→LiveEvents→Merger、回退和闪烁负例回归必须保留。
5. 先离线渲染短素材确认开头/结尾、音画同步、面板/表情和进度，再在任务里按需配置 `engine: gpu_render`。
6. 重启空闲实例，观察一次实际分段后的渲染完成消息及后续合并/上传；用户个人开关保持用户选择。不要因安装依赖自动启用任务或上传。
