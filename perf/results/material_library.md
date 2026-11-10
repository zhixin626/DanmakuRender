# 素材整理汇报（2026-11-10）

## 完成项

1. **完整复制表情包**：已把正式目录的 `emoji_pack` 完整复制到 worktree 根目录，并加入 `.gitignore`。复制后逐平台核对：bilibili 1563 个文件、douyin 436 个、douyu 267 个，共 2266 个文件、49,886,750 bytes；各平台 `emoticon/` 子目录一并保留。
2. **配置快照**：已复制 11 个配置文件到 `perf/configs_snapshot/`。`bench_render.py`、`build_full_coverage.py`、`build_final_2h.py` 均改为读取 worktree 的 venv、配置快照和 emoji_pack。
3. **按平台重做 `00_素材与旧版`**：
   - B 站：`bilibili_full_coverage_峰哥_1860-2160`，300 秒，1080×1920@25；弹幕 275、礼物 117、SC 12、会员 3，其中包含 4 条纯 emoticon（3 条真实 + 1 条 `_synthetic` `[上热榜]`）；旧版 E 300.12 秒。
   - 抖音：`douyin_不可一世杀手_01点19分`，300 秒，1920×1080@60；弹幕 126、礼物 6；旧版 E 300.033 秒。抖音平台不支持 SC/会员，本素材未硬造，只补礼物和 VIP Emoji。
   - 斗鱼：`douyu_少年不太冷_0-300`，300 秒，1920×1080@60；弹幕 26、礼物 7、SC 1、会员 1；旧版 E 300.033 秒。
4. **旧版 E 隔离渲染**：三个平台均使用 `perf/venv`、worktree `emoji_pack/<platform>`、worktree 配置和素材目录重新渲染；B 站、抖音、斗鱼各有可播放 `旧版E.mp4`。截图已放在各素材子目录，核对了 Emoji/表情包显示；B 站新增 emoticon 的可见证据是 `截图_42s_B站_emoticon.png`。
5. **目录结构**：`演示视频/` 根目录现在只保留子文件夹；旧的根目录 `说明.txt` 已移除。`00_素材与旧版/说明.txt` 记录来源、时段、事件数量、平台限制和截图；现有 `2026-11-10_02_0c_面板与闪烁修复/说明.txt` 已注明对照该素材库。
6. **路径自检**：`rg --pcre2 'DanmakuRender[\\/](?!-perf)' perf DMR` 无正式目录路径残留；`dmr_gate.py` 只读取进程状态的例外保留。历史报告中的原始来源已改成“已复制到 worktree”的描述。

## 未覆盖平台

Tasks 中没有可确认的虎牙录播，因此没有建立虎牙目录，也没有用其它平台素材冒充虎牙。

## 验证

- 三个平台素材子目录均包含 FLV、原始 JSONL、`*_augmented.jsonl` 和 `旧版E.mp4`。
- 旧版 E 输出均约 300 秒，B 站 25fps，抖音/斗鱼 60fps。
- `perf/prepare_material_library.py` 可从 worktree 副本重建目录和补充事件。

## 第 1 步中段重选

- B 站新窗口：`7月30日20点23分` 录播总长约 7.4 分钟，窗口 120–300 秒；由于原文件短于 20 分钟，严格跳过前后各 10 分钟没有可用的 3 分钟窗口，这是本项未满足的明确限制，已在 `00_素材与旧版/说明.txt` 标注。
- 抖音新窗口：`11月7日23点19分`，3600–3900 秒；斗鱼新窗口：`11月10日12点25分`，3600–3900 秒。两者均跳过前后 10 分钟并使用 5 分钟窗口。
- 新窗口均重新生成 FLV、原始/增强 JSONL、配置副本和旧版 E。新 B 站旧版 E/G 对比图为 `截图_48s_emoticon_E_G_3x.png`，E/G 同配置且平台表情图片可见。
- `EmoticonStore` 已从 `emoji_render_mt.py` 移到 `DMR/Render/sprites.py`；`prepare_gpu_plan.py` 用同一实现将 URL-only emoticon 解析到 `emoji_pack/<platform>/emoticon`，GPU 图集已成功构建。
- 已删除所有素材子目录内的 `emoticon/` 缓存；`bench_render.py` 的 `--emoticon-dir` 已改为 `emoji_pack/<platform>/emoticon`。

# CURRENT_TASK 第 1 步逐项状态

1. **中段重选：部分完成（B 站有规则阻塞）**。抖音从 11月7日23点19分录播的 3600–3900 秒选 5 分钟；斗鱼从 11月10日12点25分录播的 3600–3900 秒选 5 分钟，均跳过前后 10 分钟。B 站改选 7月30日20点23分录播，但该文件总长约 7.4 分钟，严格跳过前后各 10 分钟没有可用的 3–5 分钟窗口；已选可用的 120–300 秒最长中段并在说明中明确阻塞。
2. **补强覆盖：完成**。新 B 站补礼物六档、SC 六档、会员、VIP 和 URL-only emoticon；抖音补礼物六档、VIP、平台表情图片，SC/会员标记不支持；斗鱼补礼物六档、SC 六档、会员、VIP、平台表情图片。
3. **覆盖表：完成**。`00_素材与旧版/说明.txt` 已为新旧窗口写统一表格，所有数量使用真实+补充格式，价格档位列顺序固定。
4. **缓存路径：完成**。`bench_render.py` 改成 `emoji_pack/<platform>/emoticon`；所有素材子目录的 `emoticon/` 已删除；旧版 E 已用统一路径重新渲染。斗鱼旧版 E 的一次并发清理出现过 WinError 32，但已有完整 300.033 秒输出，未结束任何非本任务进程。
5. **GPU URL-only 表情：完成**。`EmoticonStore` 唯一实现已移动到 `DMR/Render/sprites.py`，旧版和 `prepare_gpu_plan.py` 共用；GPU 图集成功解析 B 站 `[上热榜]` URL-only 资源并生成输出。
6. **E/G 对比截图：完成**。`bilibili_峰哥_7月30日20点23分_中段/截图_48s_emoticon_E_G_3x.png` 为同一配置 E/G 并排放大 3 倍，平台表情图片可见。
7. **素材结构：完成**。每个新旧素材子目录均包含 FLV、原始 JSONL、`*_augmented.jsonl`、配置和 `旧版E.mp4`；新增 B 站子目录还包含 GPU `GPU_G.mp4` 及对比截图。

验证：`py_compile` 通过；共享 `EmoticonStore` 只在 `sprites.py` 定义；素材目录中没有 `emoticon/` 缓存副本；新 B 站 GPU 图集和 E/G 截图均生成成功。
