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
