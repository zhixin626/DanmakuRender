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

## 背景：当前链路与已知热点

链路：`ffmpeg 解码(-hwaccel auto) → fps,scale,pad,subtitles(libass) → nv12 管道 → Python 合成 Emoji → nv12 管道 → ffmpeg 编码(nvenc)`

`render_segment()` 中已确认的问题：

1. **整帧拷贝**：`readexactly` 读出 bytes，有 Emoji 的帧再做 `np.frombuffer(...).copy()` 和 `arr.tobytes()`，相当于每帧拷贝 2 到 3 次整帧（1080p 一帧约 3.1MB）。
2. **线性扫描**：主循环每帧执行 `active = [ev for ev in emoji_events if ev["s"] <= t <= ev["e"]]`，复杂度是 O(帧数 × 事件数)。
3. **float32 混合**：每个事件每帧都要做 `astype(float32)`、`A*op`、`1-aR`、`[::2, ::2]` 切片，产生大量临时数组。
4. **解码侧**：`-hwaccel` 没有指定输出格式，帧会被下载回内存。fps、scale、pad、libass 都在 CPU 上执行，改 Emoji 合成消除不了这部分开销。

## 阶段 0：建立基线（先测量，不改逻辑）

**测试素材**：选一段 5 分钟、弹幕密集且带 Emoji 的真实录像，连同对应的 RenderPlan 和 ASS 文件，路径写在结果里。所有组使用同一份输入和同一组编码参数（与当前配置一致：`hevc_nvenc -b:v 4M`）。

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

## 阶段 1：低风险 Python 优化（只有确认 D 或 E 是主要瓶颈时才做）

拆成两个提交，分别测量：

**1a：零拷贝 + 扫描线（要求输出逐字节一致）**
- 预先分配 `bytearray(fb)` 缓冲池（数量不少于 QDEPTH+2），reader 用 `readinto` 读入；用 numpy 视图原地合成；writer 直接写 `memoryview`。去掉 `copy()` 和 `tobytes()`。
- 事件按 `s` 排序，用一个指针追加新进入的事件，并移除 `e < t` 的事件。**活动列表必须保持原始 `emoji_events` 的顺序**，因为叠加顺序会影响结果。可以用原始下标排序，或者维护一个有序结构。
- 注意缓冲区复用的生命周期：缓冲区要等 writer 写完才能归还给 reader，否则会出现画面撕裂。

**1b：整数混合（允许每个采样值差 ±1）**
- 每个事件预先计算：`alpha_u8 = round(A*op*255)`；Y、U、V 精灵为 uint8；alpha 预先降采样到 UV 尺寸（注意 dx0、dy0 的奇偶会影响对齐，要处理成和旧逻辑相同的采样位置）。
- 混合公式：`dst = (src*a + dst*(255-a) + 127) // 255`，在 uint16 下计算，尽量写成原地运算。

**校验**：新增 `perf/compare_frames.py`。用环境变量 `DMR_EMOJI_DUMP=<path>` 把合成后的 nv12 原始帧写到文件（只取前 N 帧，或每隔 K 帧取一帧），新旧版本逐帧比较：
- 1a：必须完全一致。
- 1b：报告最大差值、差值大于 1 的像素占比、PSNR（应 ≥ 50dB）。另外抽 3 帧截图，人工看一下 Emoji、药丸、描边和透明度。

**输出**：`perf/results/phase1.md`（按阶段 0 的格式重测 D、E 两组，并附校验结果）。

## 阶段 2：numba 加速合成（只有阶段 1 之后合成仍占主导时才做）

- 把单个事件的 Y 和 UV 混合写成 `@njit(cache=True)` 函数，事件之间的循环仍放在 Python 里。
- `import numba` 失败时自动回退到 1b 的 numpy 实现，并打印一条 INFO 日志。
- 校验标准同 1b。

## 阶段 3：解码侧（只有阶段 0 显示 A、B、C 占主导时才做，届时由 Claude 另写计划）

可能的方向：源已是目标尺寸时跳过 scale 和 pad；调整解码线程和 `-hwaccel` 选项；评估 C++ 或 libav 单进程方案。

## 降噪（与以上各阶段独立，可选）

渲染不是实时任务，可以降低渲染子进程优先级（Windows 用 `BELOW_NORMAL_PRIORITY_CLASS`），或者限制 ffmpeg 的 `-threads`，做成配置项，默认关闭。

## 进度

- [ ] 阶段 0：基线 → `perf/results/phase0.md`
- [ ] 阶段 1a：零拷贝 + 扫描线
- [ ] 阶段 1b：整数混合
- [ ] 阶段 2：numba
- [ ] 阶段 3：解码侧（待定）
