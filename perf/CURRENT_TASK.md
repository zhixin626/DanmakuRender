# 当前任务：GPU 音画时长超差的诊断 + 合并格式检查验证

分工：代码由 Claude 修改；本任务只做测试和报告，**不要改 DMR 代码**（测试脚本可以写，放到输出目录，不提交）。
环境：使用 `D:\DanmakuRender` 的 venv；所有输出放在 `D:\DanmakuRender\test文件\av_sync_diag\`。
先 `git pull` 拉取本分支（基于 zhinxinchange_v5 最新）。完成后把报告贴到本 PR 的评论里（中文）。

## 背景

GPU 渲染结束时 `validate_av_duration` 比较输出的音频、视频总时长，相差超过 0.1 秒就抛
`RenderInputError`，不重试，直接回退到 emoji_dmrender。用户在网速慢、切换 VPN 配置时遇到过。
回退引擎的音频处理（`aresample=async=1000` + `apad`）和 GPU 一样，只是不做检查，所以回退
大概率没修好任何东西，只是更慢。决定怎么改之前，需要知道真实情况下差了多少、差在哪里。

## A. 从日志找出问题片段

1. 在 `D:\DanmakuRender\logs`（以及 WebUI 终端日志，如有）最近 14 天的日志里搜索：
   - `audio/video duration difference exceeds`
   - `回退到`
2. 每一处列出：时间、任务名、源视频路径（从前后日志行找）、报错里的 durations 数值
   （video / audio 各多少秒，谁长）、回退渲染是否成功、该场次之后的自动合并结果文件名。

## B. 对每个仍存在的源文件（最多 3 个）做分析

1. **时间戳统计**：用 ffprobe 导出源 flv 的视频包和音频包时间戳
   （`ffprobe -v error -select_streams v:0 -show_entries packet=pts_time,dts_time -of csv=p=0`，音频用 a:0），
   分别统计：
   - 时间回退（后一个包比前一个早）：次数、发生位置（第几秒）、回退幅度；
   - 向前跳（间隔大于正常间隔的 2 倍）：次数、位置、幅度；
   - 大于 10 秒的跳变或重置：次数、位置。
   并对照音频和视频：哪些跳变只出现在其中一条轨，哪些两条都有、位置是否一致。
2. **复现**：`python render_only.py --transcode --task <任务名> --video <源flv> --output <输出>`
   （GPU 不烧弹幕，走同一条编码和音频封装路径），记录是否同样报超差、数值是多少。
3. **保留输出供人工检查**：写一个测试脚本，在脚本里临时把
   `DMR.Render.gpu_render.validate_av_duration` 替换成只打印 durations、不抛错的函数，
   再调用 `gpu_render.render_one(..., without_danmaku=True)` 渲染同一个源，保留输出文件。
   对输出报告：视频轨、音频轨各自的起始 pts、结束 pts、时长；音频里是否有明显的静音补齐段。
   再从输出里截取每个跳变位置前后各 10 秒的片段（`ffmpeg -ss … -t 20 -c copy`），
   列出文件名和对应的源跳变位置，供用户自己看口型和声音是否对得上。

## C. 合并格式检查（commit b686028）

1. 找 2–3 场已有的多段 GPU 弹幕版录制，对同一场的每段运行
   `ffprobe -v error -show_data_hash sha256 -select_streams v:0 -show_entries stream=codec_name,profile,pix_fmt,r_frame_rate,extradata_hash -of compact <文件>`，
   报告同一场各段 extradata_hash 是否完全一致；不一致时列出差异（r_frame_rate 等）。
   **这是关键**：如果同一场 GPU 段的参数头本来就不同，新规则会把原本能合并的场次拆成多 P。
2. 用 `merge_only.py` 合并其中一场，确认没有被拆分、接缝处播放正常。
3. 用 `render_only.py --transcode` 转一段，和同一任务的 GPU 弹幕版一起用 merge_only 合并，确认能合并、接缝正常。
4. 若 A 中找到了回退渲染的 emoji_dmrender 段，把它和同场 GPU 段一起交给 merge_only，确认被拒绝并列出格式差异。
5. 若 A 中回退场次已被旧版 Merger 自动合并过，检查合并文件在接缝处是否花屏。

## 报告格式

按 A / B / C 分节，表格为主，每个结论注明依据（日志行、命令输出）。
发现疑似 bug 只描述现象和复现方法，不要改代码。
