# 当前任务（Codex 只需先读这个文件）

> 分工：**Claude 负责架构和设计，Codex 负责实现细节，用户提需求**。验收标准不再是"和旧版一模一样"，而是"符合配置、效果正确、用户看过满意"。旧版 E 只用来对照内容（弹幕有没有丢、时间对不对）。
> 每完成一步：逐项对照清单汇报（完成 / 未完成 / 阻塞），用 `gh pr comment --body-file` 发到 PR #1，然后停下等审查。

## 本轮：结构整合 + 四个修复（一起做，因为都改同两个文件）

### A. 结构整合：DMR 里最终只新增 2 个文件

| 现有新文件 | 去向 |
|---|---|
| `media.py` | 输出帧率规则（整数倍、最低帧率）并入现有的 `resolution.py`（和画布尺寸一样属于"输出规格"决策）；读取源帧率并入现有的 `DMR/utils/ffprobe.py` |
| `plan_types.py` | 并入现有的 `layout_models.py`（和 `RenderPlan`、`LayoutState` 放在一起） |
| `gpu_atlas.py` | 绘制代码（普通弹幕、药丸、SC/礼物/会员面板、表情）全部并入 `sprites.py`；打包图集也作为 `sprites.py` 中的一个函数 |
| `gpu_dmrender.py` | 并入 `gpu_render.py`：和 `dmrender.py` 一样，一个引擎一个文件，`GpuRender` 类和渲染流程在一起 |

最终 `DMR/Render/` 新增文件只有 **`sprites.py`、`gpu_render.py`**，其他都是修改现有文件。**能复用现有函数就复用，可以修改现有函数让它支持更多功能**，不要另写一份。

### B. 配置统一：不新增配置段
- 删除 `gpu_rc_args`。GPU 版直接解析现有的 `vencoder_args`（ffmpeg 写法）：`-b:v` → bitrate，`-maxrate` → maxbitrate，`-bufsize` → vbvbufsize，没写的项不传给编码器（使用编码器自己的默认值），**不在代码里另设默认数值**。`default.yml` 中只需要在 `vencoder_args` 的注释里说明 GPU 引擎会读取这些参数。
- 引擎切换只需修改 `engine: gpu_render`，其他配置不变。

### C. 命名
文件、类、函数、变量名要**有意义且简洁**：一看名字就知道用途，不要带阶段号、`perf`、`v2`、`new`、`tmp` 之类的字样，也不要过长。例如：`build_atlas()`、`schedule_frames()`、`draw_panel()`、`load_into_canvas()`、`mux_audio()`。重命名后同步更新调用方和测试。

### D. 四个修复
1. **画布和黑边**：编码器和缓冲区按 `resolve_render_canvas(...)` 得到的画布尺寸创建（和 RenderPlan 一致），每一帧都等比缩放并居中补黑边到画布。验收：problem_resolution 的输出是 1920×1080，有黑边，弹幕和面板的位置正确。
2. **抗锯齿**：面板和药丸的形状、描边都先按 4 倍尺寸绘制，再用 LANCZOS 缩小。
3. **圆角统一用标准圆弧（用户决定，更正上一条"按旧版贝塞尔曲线"的要求）**：药丸、SC、礼物、会员面板都用**同一个**圆角绘制函数（标准圆弧，`round_top`/`round_bottom`/stadium 三种形状）。另出一张对比图：同一个 SC 面板分别以 `corner_radius` = 40 / 30 / 20 / 15px 绘制（都加抗锯齿），供用户选择默认值。
4. **视频结尾卡住**：只有在测试有意截短视频时，`mux_audio` 才用 `-t <视频时长>` 对齐音频；完整渲染时检查音视频时长差 ≤ 0.1 秒，并确认帧调度器没有丢掉结尾的帧。

### E. 测试和检查
- 单元测试补充：画布尺寸和黑边位置、圆角形状和抗锯齿（与参考图逐像素比较）、`vencoder_args` 解析、帧调度器（25→50、VFR、结尾）。
- `verify_all.py` 增加：音视频时长差、最后 5 秒画面在动、`DMR/Render/` 中没有除 `sprites.py`、`gpu_render.py` 以外的新文件。

### F. 交付
演示视频放到新文件夹 `演示视频\<日期>_<序号>_结构整合与修复\`：problem_resolution 的 E/G 并排（看黑边）、05 的 G（看圆角和锯齿）、圆角半径对比图、02 的 G（看结尾不卡）。

---

## 以前的任务（历史，仅供参考）

## ✅ 已完成：2 小时 CPU 遥测（结果见 `perf/results/cpu_telemetry.md`）

G 相对 E：平均核数 3.45→0.94，峰值 5.5→1.5，核·秒降低 76%。B 站素材保留 8 月 18 日的中段（1860–2160 秒，本身就从中间截取）作为主素材；7 月 30 日的短录播作为"短录播"案例保留。

### 现在做（用户授权，不必等用户回来；做完第 2 步后停下）

**A. 长片码率失控（先做，≤ 1 小时）**：2 小时片 G 的码率是 8.23Mbps，已经超过配置的 `maxbitrate`（6Mbps），而短片正常（3.48Mbps）。说明在某些路径上码率控制没有生效。请：
1. 在日志中打印 `CreateEncoder` **实际收到的全部参数**（codec、rc、bitrate、maxbitrate、vbvbufsize、preset、framerate、分辨率），确认 2 小时片（60fps、1920×1080、整数倍 CFR 路径或直通路径）是否真的带上了 `gpu_rc_args`；
2. 用 ffprobe 按每分钟统计 2 小时片 G 输出的码率曲线，看是从头到尾都高，还是某段突然升高；
3. 找到原因并修复，**不要用系数凑**。用 2 小时素材中的 10 分钟片段复测，码率应 ≤ 配置值的 110%。

**B. 说清楚遥测中的 ffmpeg**：`cpu_telemetry.md` 中 G 的"ffmpeg 平均 1.217 核"大于整体平均 0.941 核，说明是只在 ffmpeg 运行期间统计的平均值。请写明 G 流程中 ffmpeg 在做什么（例如最后的音频封装）、运行了多少秒；总表中各进程一律改为"**核·秒**"，避免不同口径的平均值混在一起。

**C. 然后做第 2 步**（下文"第 2 步"全部内容：代码重构、清理、单元测试、`verify_all.py`）。第 1 步的素材视觉部分等用户回来再确认，**不影响第 2 步开始**。第 2 步完成后，逐项汇报并停下。

### 第 1 步：素材 + 表情路径

1. **重新挑选素材，从录播中间截取**：
   - 从 `D:\DanmakuRender\Tasks文件\峰哥` 挑一段新的 B 站素材（优先选和现有素材 `8月18日14点19分` 不同的录播文件）。
   - 规则（以后所有素材都适用）：跳过每个录播文件的**前 10 分钟和最后 10 分钟**，在剩下的部分中，选弹幕类型最丰富的 3 到 5 分钟窗口。
   - 抖音和斗鱼现有素材都是从 0 秒开始截的，按同样的规则**从中间重新挑选**。
   - 素材复制到 worktree 后，就只使用 worktree 内的副本。
2. **补强覆盖**：平台支持但素材里没有的类型，按补充规则（平移 → 改写 → 手写，带 `_synthetic` 标记）补齐：SC 和礼物的各个价格档、密集进入和退出、VIP 药丸、**平台表情图片**（不是 Unicode Emoji）。平台本身不支持的写"不支持"，不要硬造。
3. **覆盖表**：在 `演示视频\00_素材与旧版\说明.txt` 中，为每个平台、每段素材列一张统一格式的表：普通弹幕 / Unicode Emoji / 平台表情图片 / VIP 药丸 / 礼物（各价格档）/ SC（各价格档）/ 会员。每格写"真实数 + 补充数"。
4. **表情缓存路径统一**：`bench_render.py` 第 327 行目前是 `--emoticon-dir <视频所在目录>\emoticon`，这是错的。请改为 `emoji_pack\<平台>\emoticon`，和 `DMR/Render/emoji_dmrender.py` 第 152 行的正式行为一致。**删除** `演示视频\00_素材与旧版\bilibili_full_coverage_峰哥_1860-2160\emoticon\`，以及其他素材目录里类似的 emoticon 文件夹，然后重新渲染旧版 E，截图确认平台表情图片仍然正常显示。
5. **GPU 版支持只有 URL 的平台表情**：`prepare_gpu_plan.py` 只处理带 `path` 的图片资源，只有 `url` 的平台表情（例如 B 站 `[上热榜]`）会被跳过，导致不显示。请把 `emoji_render_mt.py` 中的 `EmoticonStore` 抽到 `DMR/Render/sprites.py`，旧版和 GPU 版共用；缓存目录同样是 `emoji_pack\<平台>\emoticon`。**不要另写一份。**
6. **自检**：搜索所有脚本，确认 emoticon 缓存只有 `emoji_pack\<平台>\emoticon` 这一个位置；用 B 站素材中的纯表情弹幕，出 E 和 G 并排、放大 3 倍的对比截图（同一套配置）。
7. 每个素材子目录仍然要包含：flv、原始 jsonl、`*_augmented.jsonl`、`旧版E.mp4`。

### 第 2 步：代码重构 + 清理 + 自动检查（用户已授权开始）

1. **正式代码移到 `DMR/Render/`**，`perf/` 只保留测试脚本：
   - `prepare_gpu_plan.py` 中的图集构建逻辑移到 `DMR/Render/gpu_atlas.py`，由 `gpu_dmrender.py` 在渲染前**自动**从 RenderPlan 生成，缓存放在任务的临时目录中，用完清理；
   - 所有绘制（普通弹幕、面板、药丸、表情下载缓存）都集中在 `sprites.py`；
   - 压力测试 kernel、帧校验值等原型和诊断代码移出 `gpu_render.py`。
2. **编码循环只保留一份**：写一个"帧调度器"（生成器），输出"源帧 + 输出时间"，统一处理直通、整数倍补帧、VFR 和结尾补帧；`gpu_render.py` 中只保留一个"叠加 → 同步 → 编码"的循环（现在有 3 份）。
3. **RenderPlan 事件和面板节点改用固定结构**（dataclass），读取时缺字段直接报错，不再使用 `.get(key, 默认值)`。
4. **单元测试 `tests/`**（不需要 GPU，用 pytest，几秒内跑完）：帧调度器（25→50 时每个源帧正好出现 2 次；VFR）、扫描线（进入、退出、叠加顺序）、面板和药丸绘制与参考图片逐像素比较（形状、字号、描边、抗锯齿、多行）、字号换算、码率解析、帧率计算。
5. **`perf/verify_all.py`**：每次提交前必须运行，任意一项失败就不能汇报"完成"。内容包括：单元测试；搜索代码中引用 `D:\DanmakuRender`（非 `-perf`）的路径；搜索可疑的 `.get(..., 默认值)` 和魔法数字；emoticon 缓存路径唯一；对指定的短输出逐帧检查闪烁；检查帧数、时长、码率（不超过配置值的 110%）；截图之间的像素差（确认画面在动）。
6. **清理 `perf/`**（删除前把清单和总大小写进汇报）：
   - 删除：`bench_phase1a.py`、`bench_phase1a2.py`、`diag_decoder.py`、`run_phase0b.py`、`bench_phase1b.py`、`compare_phase1b2.py`；`prepare_phase1b2.py` 的功能并入 `verify_all.py` 的"配置可调"检查后删除；`build_full_coverage.py`、`build_final_2h.py` 的功能并入 `prepare_material_library.py` 后删除；`__pycache__/` 删除并加入 `.gitignore`。
   - `bench_render.py` 和 `check_flicker.py` 的公共部分合并为 `perf/benchlib.py`。
   - `results/`：md 报告保留，旧截图删除，只保留最近一轮的验收截图。
   - 本地（不在 git 中）：`perf\outputs\`、`perf\tmp\` 全部清空；`演示视频\` 只保留 `00_素材与旧版` 和最近一轮的结果文件夹。
   - `PLAN.md` 中的历史内容移到 `perf/HISTORY.md`，`PLAN.md` 只保留目标、验收标准和通用规则。
7. 重构后，用单元测试和 `verify_all.py` 证明原有功能没有被弄坏：用 B 站素材，分别输出 25fps 和 50fps 的 G，与重构前逐帧对比，差异应在编码误差范围内。

### 第 3 步：1d 剩余任务（用户授权：第 2 步完成并汇报后直接继续，不必等审查；第 3 步全部完成后再停下）

1. 面板抗锯齿（如果第 2 步的单元测试已覆盖，这里只需确认）。
2. `04_G_50fps` 缺少左侧 SC 的问题：查明根本原因并修复（第 2 步的帧调度器可能已经解决，需要验证）。
3. 1d-2：E 和 G 分别与**源视频**比较无弹幕区域的 PSNR（要求 G ≥ E − 0.5dB），以及 YUV 均值差 < 1。
4. 1d-3：从 2 小时素材中取最密集的 20 分钟，测量 E 和 G 的 CPU（平均/峰值核数、核·秒）以及 GPU/NVENC/NVDEC 占用。
5. 1d-5：GPU 处理分辨率中途变化（等比缩放 + 黑边），跑通 `problem_resolution`。
