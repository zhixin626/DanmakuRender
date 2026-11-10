# 当前任务（Codex 只需先读这个文件）

> 分工：**Claude 负责架构和设计，Codex 负责实现细节，用户提需求**。验收标准不再是"和旧版一模一样"，而是"符合配置、效果正确、用户看过满意"。旧版 E 只用来对照内容（弹幕有没有丢、时间对不对）。
> 每完成一步：逐项对照清单汇报（完成 / 未完成 / 阻塞），用 `gh pr comment --body-file` 发到 PR #1，然后停下等审查。

## 本轮（用户确认，2026-11-11）

上一轮（80813e6 + render_only 相对路径修复）已审查。用户实际运行 `render_only.py` 后发现以下问题。

1. **弹幕层又闪烁了（必须先修）**
   - 原因（Claude 分析）：上一轮删除了 Encode 之后的同步，但只有**一块** `NV12Buffer`，NVENC 也是异步读取输入的。代码马上把下一帧 `load_into_canvas` 到同一块缓冲区，编码器读到了被覆盖、还没叠弹幕的画面。
   - 修复：使用 **3 到 4 块 `NV12Buffer` 轮流**（环形缓冲），每块在被复用之前，确认编码器已经消费完毕（例如在 `Encode` 返回对应的输出包之后，或者按编码器的延迟帧数轮换）。不要靠恢复多次同步来解决。
   - **检查脚本漏判**：`check_flicker.py` 报告 0，而用户肉眼看到了闪烁。请拿用户的 `D:\DanmakuRender-perf\test.mp4` 运行检查，找出漏判的原因（阈值、检查区域、只看 Y 分量……）并修正，确认修正后的检查能在 `test.mp4` 上**检出**闪烁，修复后的新输出上为 0。把这个回归用例加入测试。

2. **`render_only.py` 的交互改为逐步询问**
   1. 视频：输入 flv 路径（输入文件夹则进入原来的批量模式）；
   2. 弹幕：自动查找同名的 `.jsonl` 或 `*_augmented.jsonl`，找到就显示并让用户回车确认，找不到就让用户输入路径；
   3. 任务配置：列出 `configs/` 中的任务供用户按编号选择，回车表示使用全局配置。**不要在用户不知情的情况下自动回退到全局配置和 emoji_dmrender**；
   4. 输出文件夹：回车默认为视频所在的文件夹；
   5. 输出文件名：回车自动生成 `原文件名（弹幕版）.mp4`。
   - 批量模式中匹配不到弹幕的视频，询问用户是否手动指定，而不是直接忽略。
   - 命令行参数模式（`--task --video --danmaku --output`）保持不变。

3. **体验细节**
   - 消除 `CUDA path could not be detected` 警告：启动时根据 venv 中 nvidia 运行库的位置设置 `CUDA_PATH`（不要写死绝对路径）；
   - 进度显示末尾的残留字符（`100.0%   05490886%`）：每次输出前清除整行。

4. **补强 B 站中段素材的弹幕**（用户要求）：在 `演示视频\00_素材与旧版\bilibili_峰哥_7月30日20点23分_中段\7月30日20点23分_中段_augmented.jsonl` 中补充（带 `_synthetic` 标记，原始 JSONL 不改）：
   - **更多礼物和 SC**，覆盖全部价格档；
   - **同一时刻多个礼物同时进入**（同一个时间戳 5 个以上，礼物和会员混合），以及 **10 秒内连续进入 8 条以上**；
   - **退场**：不同持续时间的礼物和 SC 陆续退出，剩余的面板重新排列；也要有多个面板在同一时刻退出的情况；
   - **平台表情 emoticon**：至少 5 条纯表情弹幕（只有 URL、需要下载缓存的），另有几条"文字 + `[表情]`"混排的弹幕；
   - 更新 `说明.txt` 中的覆盖表，并写明各个场景发生在第几秒。然后用旧版 E 和 GPU 版 G 各渲染一次，输出到新的演示文件夹，并对 emoticon、同时进入、同时退场这几个时刻分别截取 E/G 并排、放大的对比图，**确认 emoticon 在 G 中能正确显示**。

5. **之前遗留的几项**
   - **开头丢 2 帧**：裸 HEVC 流封装时开头时间戳为负，导致前两帧不显示。请在封装时修正时间戳（例如为裸流设置正确的起始时间，或者使用 `-avoid_negative_ts make_zero`），确认输出的第一帧就是源视频的第一帧；
   - **配置注释**（`min_fps` 的值**不改**）：`engine` 的注释中加入 `gpu_render`；`min_fps` 的说明改为整数倍规则，删除过时的中文说明和新加的英文注释，统一用中文；"以下参数只供 emoji_dmrender 使用"改为同时适用于 `gpu_render`；
   - **`dmr_gate.py` 识别 worktree 中运行的 DMR**（例如 `DMR-月亮3.bat` 启动的实例）：它渲染时也要避让。

6. **引擎与 DMR 的约定统一（用户确认，修复 BAT 实测中 `onRenderEnd` 崩溃）**
   - **返回值约定**：在 `baserender.py` 的 `BaseRender` 中写明 `render_one(video, output, **kwargs) -> tuple[bool, VideoInfo | Exception]`，并新增一个公共方法生成输出的 `VideoInfo`（复制源 VideoInfo，设置 `dtype="dm_video"`、`path`、**画布分辨率**、`file_id`、`size`、`ctime`、`dm_file_id=None`、`src_video_id`，保留 `group_id`/`segment_id` 等身份信息）。`dmrender.py`、`emoji_dmrender.py`、`gpu_render.py` **全部改为调用它**，删除 dmrender/emoji_dmrender 中复制的那两段代码。GPU 的统计数据只写日志，不放进返回值。
   - **引擎登记表**：在 `DMR/Render/__init__.py` 中定义**唯一**一份引擎登记表（名称 → 类、显示名称、是否支持 raster/药丸）。`liveevents.py`、`Render/__init__.py`、`layout_compiler.py`、`render_only.py` 中所有写死的引擎名单都改为查这份表；配置里出现未知的引擎名时**直接报错**，不要悄悄回退。测试中搜索代码，确认 `'emoji_dmrender'`、`'gpu_render'` 等字符串只出现在登记表和各自的引擎文件中。
   - **衔接点检查**：GPU 失败时回退到 E；用户停止时不回退、不重试，且 DMR 收到的消息被正确识别为"已停止"；进度消息的格式和 E 一致。
   - **主流程回归测试（必须）**：不启动 `main.py`，直接构造 `Render` 插件 + `LiveEvents`，模拟"录制分段完成 → 派发 gpu_render → 完成消息 → `onRenderEnd`"，断言状态变为 ready，并且后续 `reconcile`（合并/上传派发）正常。对 E 和 G 各跑一遍；再覆盖 G 失败回退、G 被停止两种情况。**只验证 render_only 能输出 mp4 不算通过。**
   - 封装改用 PyAV 后出现了 x265 初始化日志（`encoded 0 frames`）：确认 `opaque=True` 后日志消失、封装只复制数据，并在汇报中写明。
   - 完成后提醒用户：需要在月亮3实例空闲时重启 `DMR-月亮3.bat`，修复才会生效。

完成后逐项汇报，演示视频放进新文件夹，然后停下。

---

## 上一轮（已完成）


上一轮（结构整合 + 四个修复，cfd93c5）已审查通过。本轮清单：

1. **接入 DMR 的两个功能**：
   - **渲染进度**：`render_one` 不再丢弃 `progress_cb`，按"已编码帧数 / 预计总帧数"定期回调百分比（和 `emoji_dmrender` 一样，WebUI 的渲染进度条要会动）；
   - **中途停止**：`GpuRender.stop()` 设置停止标志，编码循环每帧检查，收到后尽快退出，释放编码器、解码器和显存，并删除未完成的输出文件。写单元测试模拟停止。
2. **清理**：
   - 删除 `gpu_render.py` 中的压力测试 kernel（`PRESSURE_KERNEL`、`_apply_pressure`、`pressure_sprites`）和诊断用的 `frame_signature`（如果测试需要，放到 `perf/`）；
   - `probe_timeline` 改用（必要时扩展）`DMR/utils/ffprobe.py` 中已有的函数，不要再自己调用 ffprobe；
   - "识别直播平台"的函数（目前从 `emoji_dmrender._detect_platform` 借用）移到公共位置（例如 `sprites.py` 或 `utils`），新旧两版共用；
   - 编码前只保留**一次**同步（`Stream.null.synchronize()` 或 `deviceSynchronize()` 二选一），删除编码后的同步；用逐帧闪烁检查确认没有回归，并记录速度变化。
3. **弹幕图集直接在内存中传递**：`build_atlas()` 返回数组，`GpuRender` 直接使用，不再写入临时目录再读回。顺带评估"相同内容的精灵去重"能省多少显存（只汇报数据，暂不实现）。
4. **`render_only.py` 支持 GPU**（复用现有脚本，不另写新脚本）：
   - `render_args.overlay.engine: gpu_render` 时使用 GPU 引擎；
   - 新增单文件模式：`python render_only.py --task <任务名> --video <flv> --danmaku <jsonl> --output <mp4>`，读取 `configs/` 中该任务三层合并后的配置；
   - 只依赖 `DMR/` 和 `configs/`，**不依赖 `perf/`** 中的任何文件；用 B 站素材实际跑一遍，截图确认。
5. **venv 移动**：`perf\venv\` 移到 `D:\DanmakuRender-perf\venv\`（加入 `.gitignore`），同步更新所有脚本、`run_gpu_render.py` 和文档中的路径；`requirements.txt` 也移到仓库根目录或合并进现有的依赖文件。
6. **检查迁移到 `tests/`**：`verify_all.py` 中可以长期使用的检查（不依赖 perf 素材的部分）迁移为 `tests/` 中的测试；`perf/` 暂时保留，最后合并进 DMR 时不带进去。
7. **清理 `perf/`**：删除 `prepare_gpu_plan.py`（功能已在 `sprites.py`）；`results\` 中只保留各阶段的 md 报告和**最近一轮**的截图，其余旧截图删除；清空 `perf\tmp\`。删除前把清单和大小写进汇报。
8. **为主播「月亮3」写任务配置**（用户要求）：
   - 参照 `configs/` 和 `perf/configs_snapshot/` 中现有的任务配置格式（例如 `DMR-峰哥.yml`），写 `configs/DMR-月亮3.yml`；
   - 平台和直播间地址：先在 `Tasks文件\`、配置快照、录播文件名或 JSONL 头部中查找「月亮3」的信息；**找不到就不要猜**，把 URL 留空并在汇报中向用户询问；
   - 渲染引擎设为 `gpu_render`，其余参数沿用全局配置，只写需要覆盖的项，并加上简短注释；
   - **只写在 worktree 中**，不要复制到正在运行的 `D:\DanmakuRender\configs\`；文件中设置 `enable: false`，由用户确认后自己启用。

完成后逐项汇报，演示视频（render_only 单文件模式的输出）放进新文件夹，然后停下。

---

## 上一轮（已完成）：结构整合 + 四个修复
（一起做，因为都改同两个文件）

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
