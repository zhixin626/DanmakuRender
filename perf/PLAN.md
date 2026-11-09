# Emoji 渲染性能优化计划

> 本文件是 Claude（云端）与 Codex（本地）之间的唯一任务清单。
> 分支：`perf/emoji-render`（从 `zhinxinchange_v5` 分出），最终以 PR 合并回 `zhinxinchange_v5`。

## 给 Codex 的说明

- **角色**：你在用户本地（Windows + NVIDIA）负责实现、运行和验证。Claude 只负责计划和审查，无法在本地运行代码。
- **范围**：只改 `DMR/Render/emoji_render_mt.py`，以及新增 `perf/` 下的脚本和结果。不要改其他模块和配置默认值。
- **约束**：
  - 默认输出画面必须与旧版一致（标准见第 2 节）。
  - 不新增必须安装的依赖。可选依赖（例如 numba）不可用时，必须自动回退到 numpy 实现。
- **流程**：每完成一个阶段就提交并推送，在下方「进度」里打勾，填上结果表，然后停下来，等用户确认后再进入下一阶段。
- **结果**：写入 `perf/results/<阶段>.md`。每份结果都要包含机器信息（CPU 和 GPU 型号、ffmpeg 版本）。
- **输出视频必须自检后才能汇报（1a-2 教训）**：每生成一个视频，都要 (1) 用 `ffprobe` 确认宽高与源一致、有音频流、时长与源相差 ≤ 0.5 秒；(2) 用 ffmpeg 在 10%、50%、90% 处各截一张 PNG，自己查看是否花屏、错位、斜切；截图提交到 `perf/results/shots/<阶段>/`。(3) **用脚本比较三张截图的像素差**（平均绝对差应明显 > 1），确认画面在动，不是同一帧（1b-0 就出现过整段视频都是同一帧、却被判定为通过的情况）；同时把 1 秒和 2 秒处的截图与源视频同一时刻的截图对比，确认画面内容对得上。没做完这两步，不能报告"成功"。
- **时间控制（阶段 1 起生效）**：每个阶段的测量和运行控制在 1 到 2 小时内。完整重复测量（3 次）只在性能基准素材上做；其他素材只跑 1 次，用来产出视频和检查正确性；背景 CPU 采样 10 秒即可。原型阶段拿到关键数字就停下汇报，不追求一步到位。如果预计会超时，先在 PR 里说明并等待确认。
- **沟通**：本分支有一个 PR，Claude 已订阅。每完成一个阶段，或遇到需要决策的问题，都在 PR 里发一条评论，写清结论和结果文件路径。Claude 会被自动唤醒并在 PR 里回复。

## 隔离：绝对不能影响用户正在运行的 DMR

用户电脑上**正在运行 DMR**（录制、渲染、上传）。下面每一条都必须遵守：

1. **在独立文件夹里工作，用 git worktree**。不要在 DMR 运行的仓库目录里切换分支或修改文件。原因是 DMR 每次渲染都会以子进程方式重新加载 `emoji_render_mt.py`，在原目录改代码会立刻影响正在跑的程序。
   ```
   # 在 DMR 所在仓库目录执行（只新增一个兄弟目录，不改动当前工作区）
   git fetch origin perf/emoji-render
   git worktree add ../DanmakuRender-perf perf/emoji-render
   ```
   之后所有操作都在 `../DanmakuRender-perf` 里进行。
2. **只读用户的数据**。`Tasks` 文件夹里的录像和弹幕文件只能读和复制，不能修改、移动或删除。正在录制中的文件（最近几分钟还在变大的）不要碰。
3. **测试产物只放在 worktree 内的 `perf/tmp/`**（已加入 `.gitignore`，不提交），不要写进 DMR 的输出目录、`.temp/`、`configs/`、`logs/`、`.login_info/`。
4. **不启动 DMR 本身**（`main.py`），不占用 WebService 端口，不调用 DMR 的上传或清理功能。基准测试只直接调用渲染脚本和 ffmpeg。
5. **不结束任何不是自己启动的进程**，尤其是 DMR 的 python 和 ffmpeg 进程。

## 测量时要考虑 DMR 的干扰

DMR 在后台录制，会持续占用 CPU、磁盘和网络，正在渲染时还会占用 GPU 编码器。为此：

- **开始前确认 DMR 当前没有渲染或合并任务**：查看 WebUI 的队列页，或者看进程列表里有没有 DMR 起的渲染 ffmpeg 或 `emoji_render_mt.py`。如果有，就等它结束再测，并在结果里注明。
- **每组测量前采样 30 秒空闲背景 CPU%**（全系统），记为 `bg_cpu`。结果里同时给出被测进程树的 CPU%（主指标）和全系统 CPU%。
- **每组跑 3 次取中位数**，各组交替执行（A B C … A B C …），避免某一段时间的背景负载只影响某一组。
- 如果某次测量期间 DMR 开始了渲染（可以通过进程监控发现），就丢弃这次结果并重测。
- 在结果中记录测量时 DMR 的状态：录制了几个直播间、是否在渲染。

## 背景：当前链路与已知热点

链路：`ffmpeg 解码(-hwaccel auto) → fps,scale,pad,subtitles(libass) → nv12 管道 → Python 合成 Emoji → nv12 管道 → ffmpeg 编码(nvenc)`

`render_segment()` 中已确认的问题：

1. **整帧拷贝**：`readexactly` 读出 bytes，有 Emoji 的帧再做 `np.frombuffer(...).copy()` 和 `arr.tobytes()`，相当于每帧拷贝 2 到 3 次整帧（1080p 一帧约 3.1MB）。
2. **线性扫描**：主循环每帧执行 `active = [ev for ev in emoji_events if ev["s"] <= t <= ev["e"]]`，复杂度是 O(帧数 × 事件数)。
3. **float32 混合**：每个事件每帧都要做 `astype(float32)`、`A*op`、`1-aR`、`[::2, ::2]` 切片，产生大量临时数组。
4. **解码侧**：`-hwaccel` 没有指定输出格式，帧会被下载回内存。fps、scale、pad、libass 都在 CPU 上执行，改 Emoji 合成消除不了这部分开销。

## 阶段 0：建立基线（先测量，不改逻辑）

**测试素材**：由你自己从用户的 `Tasks` 文件夹里找，不需要问用户。

1. 扫描 `Tasks` 文件夹，找出**已经录完**的录像和对应的弹幕文件（分段 JSONL、RenderPlan 或 ASS，按 `emoji_dmrender` 当前实际使用的输入为准）。读一下 `DMR/Render/__init__.py` 和 `emoji_dmrender.py`，确认渲染时输入的是哪些文件、怎么生成的。
2. 选 1 到 2 段**弹幕密集、带 Emoji、表情包或 SC 药丸**的素材。用 raster 事件数或 Emoji 弹幕数挑选，在结果里写明挑选理由和原始路径。
3. 如果视频太长，就**截取一段约 5 分钟的片段**，复制到 `perf/tmp/`：
   - 视频建议从 0 秒开始截取：`ffmpeg -i <src> -t 300 -c copy perf/tmp/clip.flv`，这样弹幕时间轴不需要平移。如果必须从中间截，弹幕时间也要同步平移。
   - **弹幕输入也要同步裁剪到同一时长**。注意 `emoji_render_mt.py` 会用 RenderPlan 里的时长覆盖探测到的时长（搜索「使用RenderPlan统一媒体时长」），只截视频不改 RenderPlan，会导致帧数对不上。裁剪后跑一遍渲染，确认日志里的时长和帧数正确。
   - 原文件不做任何修改。
4. 所有组使用同一份素材和同一组编码参数（与用户当前配置一致：`hevc_nvenc -b:v 4M`，hwaccel auto）。

新增 `perf/bench_render.py`，依次运行以下各组，每组记录**墙钟时间、处理 fps、平均与峰值 CPU%**（进程树合计，用 psutil 每 0.5 秒采样一次）：

| 组 | 内容 | 用来衡量 |
|---|---|---|
| A | 只解码，`-f null` | 解码 |
| B | A + `fps,scale,pad` | 滤镜 |
| C | B + `subtitles`（与 emoji 版使用同一个 textonly ASS） | libass |
| D | 完整 emoji 管线，但**跳过合成**，帧直接透传 | 管道与拷贝 |
| E | 完整 emoji 管线（当前实现） | 合成 |
| F | 普通 `dmrender` 渲染同一素材（作为对照） | — |
| G | 在 E 的基础上，把 `-c:v` 换成另一种编码器（可选） | 编码影响 |

- D 的实现方式：在 `emoji_render_mt.py` 中增加环境变量 `DMR_EMOJI_BYPASS=1`。设置后主循环跳过合成，把 `buf` 原样转发。默认行为不变。
- E 运行期间，用 `py-spy top --pid <worker pid>` 或 `py-spy record` 采样 30 秒，把火焰图或 top 结果的前 15 行写进结果。
- 另外记录：素材的总帧数、raster 事件总数、平均同屏活动事件数（可以在 worker 中加 debug 统计）。

**输出**：`perf/results/phase0.md`，包含上面的表格和 py-spy 结果。**完成后停下**，等 Claude 根据数据决定下一步。

## 阶段 0b：修正测量后补测（阶段 0 审查结论，先于阶段 1）

阶段 0 的数据有两处会导致分组之间无法比较，需要先修正：

1. **帧率不一致**：`bench_render.py` 用 ffprobe 的 `r_frame_rate`（该素材为 50fps），A、B、C 都按 50fps 处理了约 15000 帧；而 worker 用 PyAV 的 `guessed_rate/average_rate`（25fps），D、E 只处理了 7505 帧。所以 C（78 秒）不能和 D、E（48 秒）直接比，"处理 fps"一列也偏大。
   - 修正：A、B、C 的 `fps=` 使用与 worker 完全相同的取值（直接复用 worker 的探测逻辑，或者从 E 的 stats 读取）；F 也确认用的是同一帧率。
   - 每组记录**实际输出帧数**（ffmpeg 用 `-progress` 或日志中的 `frame=`；worker 用 stats），"处理 fps"按实际帧数计算。
2. **直接运行 ffmpeg 的组（A、B、C），进程树 CPU 明显偏低**：例如 B 的进程树平均 28%，但全系统扣除背景后约等于 680%（28 线程 × 24.3%）。D、E、F 两种算法的结果基本吻合。需要查明原因并修正；修好之前，结果里先同时给出 `(全系统 − bg) × 逻辑核数` 作为对照。

补测内容：

- **新增指标**：**CPU 核·秒** = 进程树平均 CPU% / 100 × 墙钟秒数，代表总工作量（耗电和发热）；**平均占用核数** = 进程树平均 CPU% / 100，代表同一时刻的负载（风扇噪音）。两项都写进结果表。
- **D、E 按进程拆分 CPU**：分别给出解码 ffmpeg、Python worker、编码 ffmpeg 三个进程各自的平均 CPU%。这是判断瓶颈的关键数据。
- **安装 py-spy**：`pip install py-spy`，只装进 worktree 的虚拟环境。给 E 的 worker 采样一次，前 15 行写进结果。
- **重新挑选素材并分类（用户要求，必须执行）**：当前素材只有 124 个 raster 事件，平均同屏 3.7 个，Emoji 负载偏轻；而且中途切换分辨率，会让 scale 的开销偏高。请从 `Tasks` 文件夹重新挑选，**分类存放**：
  ```
  D:\DanmakuRender-perf\perf\samples\        ← 截取后的素材（视频 + 对应弹幕输入）
    normal_dense\      正常：1080p 横屏、分辨率不变、Emoji/表情/SC 密集（目标 raster ≥ 500，平均同屏 ≥ 10），至少 1 段【性能结论以此为准】
    normal_light\      正常：弹幕较少的普通场次，1 段
    problem_resolution\  有问题：分辨率中途切换（当前这段归到这里）
    problem_<类型>\    有问题：其他问题类型，按 Tasks 里「有问题的视频测试」目录的实际分类建，例如横竖屏切换、花屏/损坏包、帧率不稳、音画不同步等
  D:\DanmakuRender-perf\perf\outputs\<阶段>\<分类>\<素材名>\   ← 渲染结果，供用户观看
    E_emoji_dmrender.mp4   当前 emoji 渲染器输出
    F_dmrender.mp4         普通 dmrender 输出（对照）
  ```
  - 每段截取约 5 分钟（问题素材要**截取包含问题发生时刻**的那 5 分钟，弹幕时间轴要同步平移）。
  - `samples/` 和 `outputs/` 都加入 `.gitignore`，不提交视频文件。
  - 新增并提交 `perf/samples.md` 清单：每段素材的分类、原始路径、截取区间、选择理由、分辨率/帧率、raster 事件数、平均与峰值同屏事件数，以及对应输出文件的路径。
  - 每段素材的 E、F 输出各保留一份完整视频（3 次重复中保留任意一次即可，不要被后续运行覆盖）。
  - **性能对比以 `normal_dense` 为准**；问题素材主要用于检查画面是否正确，也要测 E、F 的时间和 CPU，但单独列表。
- 每组仍跑 3 次，各组交替执行。
- **输出**：`perf/results/phase0b.md`。完成后在 PR 下评论，然后停下。

## 最终目标与验收标准（用户确认）

**目标**：在渲染速度和画质不下降的前提下，让电脑风扇尽可能安静，最好完全静音。

路线：阶段 1 用 Python 做全 GPU 渲染器；如果它达不到标准，阶段 2 改用 C++/libav + CUDA。原先的 CPU 侧 Python 优化（零拷贝、整数混合、numba）**不再单独做**，现有渲染器保持不变，作为回退路径。

验收以阶段 0b 中 `normal_dense` 素材上的当前 emoji 渲染器（E）为基准：

| 项目 | 标准 |
|---|---|
| 速度 | 墙钟时间 ≤ E |
| CPU（噪音） | 进程树平均 ≤ 0.5 核，峰值 ≤ 1.5 核 |
| 画质 | 与 E 输出对比：没有弹幕的区域 PSNR ≥ 45dB；弹幕区域由用户目视确认（字体、描边、Emoji、表情包、SC 药丸、透明度、运动是否顺滑） |
| 正确性 | 所有 `problem_*` 素材都能正确输出：分辨率切换、帧率不稳、损坏包等情况不能花屏、错位或崩溃；时长和音画同步与 E 一致 |
| 回退 | 没有 NVIDIA 显卡、依赖缺失或运行出错时，自动回退到现有 emoji 渲染器，并打印日志 |

每个阶段的输出视频都按阶段 0b 的目录结构放进 `perf/outputs/<阶段>/...`，供用户观看。

## 阶段 1：Python 全 GPU 渲染器（PyNvVideoCodec + CuPy）

先做原型验证可行性，再补全功能。新代码放在新文件 `DMR/Render/gpu_render.py` 中，不修改现有渲染器的默认行为。

**1a：原型（只验证性能，不管画质）**
- 链路：PyNvVideoCodec 解码，帧留在显存（NV12）→ 用 CuPy 的 RawKernel 把若干张 RGBA 精灵按坐标和 alpha 混合到 NV12 上 → PyNvVideoCodec 编码，使用 HEVC 和 4M 码率，参数与 E 一致 → 封装输出。音频直接复制。
- 精灵在开始时一次性上传到显存；每帧在 CPU 上只计算活动事件的坐标（用扫描线，不遍历全部事件）。
- 用 `normal_dense` 素材，**以 RenderPlan 里的全部事件（raster 和 ass）都当作精灵**（文字可以先用 Pillow 简单画，不求一致），测墙钟时间、CPU（按进程拆分）、GPU 占用（`nvidia-smi dmon` 采样）。
- **压力测试**：现有素材的 Emoji 都不密集，因此额外用同一视频叠加合成精灵：同屏 50 个和 200 个、尺寸 300×60、从右向左滚动，各测一次，看 CPU 和速度是否随同屏数量明显上升。
- 对比对象：阶段 0b 中 `normal_dense` 的 E（24.2 秒，3.47 核，84.6 核·秒）和 F（21.9 秒，1.55 核，33.8 核·秒）。
- 时间控制：每项只跑 1 次，背景采样 10 秒；只要原型能跑通并拿到上面三组数字，就停下汇报，**总时长不超过 2 小时**。如果卡在依赖安装或接口问题上超过 1 小时，就在 PR 里说明卡点并停下。
- 如果 PyNvVideoCodec 自带的读文件功能处理不了直播 flv（损坏包、时间戳），改为由 ffmpeg 负责读文件和拆包（`-c:v copy -f hevc/h264 -` 输出压缩码流），再交给 PyNvVideoCodec 解码。
- **输出**：`perf/results/phase1a.md`。然后停下，由 Claude 判断是否继续。

**1a-2：打通 NVENC，产出可播放视频（时间上限 1 小时，1a 审查后追加）**

1a 已证明"显存解码 + CuPy 合成"可以把 CPU 降到 0.25 到 0.45 核。只差编码这一环：

- **报错原因推测**：`encoder.Encode()` 收到的是 CuPy 数组。PyNvVideoCodec 判断 GPU 输入的方式，应该是看对象有没有 `cuda()` 方法（返回各平面的 CAI 列表，和解码帧对象一样），而不是看 `__cuda_array_interface__`。所以 CuPy 数组被当成了 CPU 缓冲区，报 "incorrect usage of CPU input buffer"。请对照 PyNvVideoCodec 自带的 GPU 输入编码示例（AppEncode 类示例中给帧对象实现 `cuda()` 的写法）确认。
- **按顺序尝试，任意一种成功就停**：
  1. 直接把解码得到的帧对象（已在原地合成）传给 `encoder.Encode(decoded)`；
  2. 自己写一个包装类，实现 `cuda()`，返回 `[Y 平面, UV 平面]`，两个平面都带 CAI，Y 形状 `(H, W, 1)`，UV 形状 `(H/2, W/2, 2)`，strides 按实际 pitch 填；
  3. 前两种都不行：退而求其次，用 `cp.asnumpy` 把合成后的 NV12 拷到页锁定内存，然后用 CPU 输入模式编码。这样会多一次显存到内存的下载，但没有管道和格式转换，CPU 预计仍然很低。记录这一路的 CPU 数据。
- `Encode` 返回的码流写入 `.hevc` 文件，最后用 `ffmpeg -i out.hevc -i <源> -map 0:v -map 1:a -c copy` 封装成 mp4（帧率按 worker 的帧率设置）。输出放到 `perf/outputs/phase1a/normal_dense/<素材>/G_gpu_proto.mp4`，供用户观看（画面上只有压力测试的方块，属于正常现象）。
- **顺手修正**：`width`/`height` 取自 `planes[0].shape`，而它的顺序是 (H, W, …)，竖屏素材的宽高被取反了，请改正；`ROOT = parents[1]` 指向的是 `DMR/`，不是仓库根目录，请改正 venv 的路径。
- 结果追加到 `perf/results/phase1a.md`：完整链路（解码 + 合成 + 编码 + 封装）的墙钟时间、平均和峰值核数、核·秒、GPU/NVENC 占用，用 91 和 200 两档测试。完成后在 PR 下评论并停下。

**1b：功能补全**

> 1a-2 审查后追加：1b 工作量较大，按时间控制拆成 4 个小步骤，**每步 ≤ 2 小时，做完一步就汇报并停下**。下面原有的条目是 1b 的总体要求，各步骤只做自己负责的部分。
>
> - **1b-0（先做，≤ 30 分钟）：验证 1a-2 的花屏修复**。1a-2 的输出视频花屏（斜切条纹）而且没有声音。Claude 已修复 `gpu_render.py`（提交见 PR 评论）：宽高读取错误（CAI shape 是 (H, W)，竖屏被当成横屏）、行跨距被写死成 width（NVDEC 表面的 pitch 通常更大）、音频映射改为必选 `-map 1:a`。请重新跑 91 档，按上面的自检规则验证；如果仍然没有声音，用 ffprobe 检查截取的素材本身有没有音频流。
> - **1b-0b（≤ 30 分钟）：修复"整段都是同一帧"**。1b-0 的视频有声音，但画面始终是同一帧（三张截图像素差为 0）。Claude 已修改 `gpu_render.py`：改为把每个解码帧拷贝到我们自己持有的固定 NV12 缓冲区（`NV12Buffer`），在缓冲区上合成，再交给 NVENC；同时记录解码帧的校验值（`decoded_signatures`）。请重新跑 91 档：若 `decoded_frames_change` 为 false，说明是解码器的问题（例如 `get_batch_frames` 一直返回同一帧），请在 PR 里汇报，不要继续；若为 true 而输出仍是静止画面，同样汇报。按更新后的自检规则验证（含截图像素差）。
> - **1b-0c（≤ 45 分钟）：定位"解码器在约第 60 帧后停止变化"**。1b-0b 的数据显示：`decoded_signatures` 只有前 6 个采样（约前 60 帧）在变，之后保持不变；输出从 10% 起就几乎不动（10%→50% 平均差 0.03）。所以问题出在**解码端**，不在编码端（`decoded_frames_change=true` 只说明"至少变过一次"，不能说明解码正常）。请写一个**只解码**的脚本 `perf/diag_decoder.py`，不编码，对每一帧记录 Y 平面的全图校验值（`cp.sum`）以及帧对象上能拿到的时间戳或 pts，输出"最后一次变化发生在第几帧"。依次试以下变体，**每个变体只跑前 1000 帧**，汇总成表：
>   1. 现状：`SimpleDecoder(use_device_memory=True, need_scanned_stream_metadata=False)`，`get_batch_frames(16)`；
>   2. 改成 `get_batch_frames(1)`；
>   3. 在 `get_batch_frames` 之后、拷贝之前，调用 `cp.cuda.runtime.deviceSynchronize()`；
>   4. `need_scanned_stream_metadata=True`；
>   5. `use_device_memory=False`（帧返回到内存），看 CPU 侧的数据是否在变；
>   6. 改用 PyNvVideoCodec 的底层接口：`CreateDemuxer` + `CreateDecoder`，按 `demuxer → packet → decoder.Decode(packet)` 的方式解码（官方示例的写法）。
>
>   另外用 ffmpeg 对同一素材输出前 1000 帧的 Y 平面（`-f rawvideo -pix_fmt gray`），计算每帧校验值作为"正确答案"。找到能让 1000 帧都正常变化的变体后，把它接进 `run_full`，重新生成 91 档视频并按自检规则验证；如果 6 个变体都不行，就汇报并停下。结果写入 `perf/results/phase1b0c.md`。
> - **1b-1：真实弹幕 + 流水线并行**
>   - (1) 解码和编码重叠执行：1a-2 中 NVENC 和 NVDEC 的利用率都只有约 30%，墙钟时间 24.2 秒，相当于解码时间加编码时间串行累加。用两个线程（或 CUDA stream）加一个有界队列，让解码、合成、编码重叠起来，目标 ≤ 15 秒。注意帧对象的生命周期：帧在编码完成之前不能被解码器复用。
>   - (2) 用 RenderPlan 的**真实事件**替换合成方块：raster 事件复用 `emoji_render_mt.py` 里 `get_sprite` 的绘制逻辑（可以抽成公共函数），ass 事件先用 Pillow 按 RenderPlan 的字体、字号、颜色、描边画成精灵；精灵合成到一张图集里一次性上传；kernel 同时处理 Y 和 UV（UV 按 2×2 采样 alpha）；位置按 RenderPlan 的 path 插值。
>   - 输出 `perf/outputs/phase1b/normal_dense/<素材>/G_gpu.mp4`，和 E 并排截取 5 张对比图（`perf/results/phase1b_shots/`，可以提交）。记录耗时和 CPU。
> - **1b-2：文字画质对齐**：逐帧对比 G 和 E 的输出，调整字体渲染（hinting、描边、阴影、透明度）；给出无弹幕区域的 PSNR 和弹幕区域的差异热力图；由用户目视确认。
> - **1b-3：问题素材**：在 GPU 上实现 VFR→CFR 和分辨率中途变化时的缩放加黑边；跑通所有 `problem_*` 素材，输出视频供观看。
> - **1b-4：接入 DMR**：新增 `engine: gpu_render` 配置，出错时自动回退到 `emoji_dmrender`；用全部 4 类素材做一次最终验收。

- **普通弹幕也改成精灵**：根据 RenderPlan 中所有事件（包括原本走 libass 的 ass 模式事件）预先绘制精灵，彻底去掉 libass。文字绘制要尽量接近 libass 的效果（字体、字号、描边、阴影），用 E 的输出逐帧对比调整。
- 在 GPU 上实现 VFR→CFR（按时间戳选帧或重复帧，与 ffmpeg 的 `fps=` 行为一致）、分辨率中途变化时的等比缩放加黑边补齐（与现有 `scale+pad` 一致）。
- 接入 `emoji_dmrender` 的配置：新增 `engine: gpu_render`，或者用开关控制；出错时自动回退。
- 用全部素材分类测试，输出视频供用户观看；画质对比脚本输出 PSNR 和差异热力图截图。
- **输出**：`perf/results/phase1b.md`，并在 PR 下评论，附上输出视频的路径。

## 阶段 2：C++ / libav + CUDA（只有阶段 1 达不到验收标准时才做）

触发条件（满足任意一条）：Python 版 CPU 无法达标；读文件或解码在问题素材上无法稳定处理；性能波动大。届时由 Claude 根据阶段 1 暴露的问题另写详细计划。思路是用 libavformat/libavcodec（NVDEC/NVENC）加 CUDA 混合 kernel 写成单个进程，Python 只负责传入 RenderPlan 和精灵。

## 降噪（可与以上各阶段并行，可选）

在 GPU 渲染器完成之前，可以先给现有渲染器加一个配置项：降低渲染子进程优先级（Windows 的 `BELOW_NORMAL_PRIORITY_CLASS`）和/或限制 CPU 亲和性（核数）。默认关闭，作为临时降噪手段。

## 附录：已搁置的 CPU 侧优化

零拷贝 + 扫描线、整数混合、numba：只在需要改进回退路径时再考虑。原始设计见 git 历史中 `perf/PLAN.md` 的旧版本（提交 5306001）。

## 进度

- [x] 阶段 0：基线 → `perf/results/phase0.md`
- [x] 阶段 0b：修正测量，重新挑选并分类素材，保留输出视频 → `perf/results/phase0b.md`
- [x] 阶段 1a：Python GPU 原型 → `perf/results/phase1a.md`（解码、合成和 NVENC 闭环已通过）
- [x] 阶段 1a-2：打通 NVENC，产出可播放视频
- [ ] 阶段 1b-0：验证 1a-2 输出自检（1b-0、1b-0b 均未通过：输出画面静止；1b-0c 定位解码器问题）
- [ ] 阶段 1b：Python GPU 功能补全，画质与问题素材验证 → `perf/results/phase1b.md`
- [ ] 阶段 2：C++/libav + CUDA（按需）
