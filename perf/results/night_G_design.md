# G：分辨率时间线与整场画布策略设计

开始时间：2026-11-11 04:08 Asia/Singapore；预计实现与测试 15–20 分钟。

1. **时间线位置**：在现有 `DMR/Render/resolution.py` 增加纯函数，输入 `[(start, end, width, height)]`，过滤开头 `ignore_head_seconds`、短于 `min_span_seconds` 的 span，按方向累计时长并返回主方向/画布。生产扫描入口复用 `DMR/utils/ffprobe.py` 的 ffprobe 包装，不新增生产模块。
2. **整场判定**：新增 `decide_session_parts(spans, minority_ratio, minority_minutes)`；同方向返回一个 part；少数方向满足任一阈值则返回 `re_render_minority`，否则按连续方向 run 返回多个 part。保留 `portrait`/`landscape` 强制画布选项。
3. **接线边界**：当前 `Merger` 已按连续分辨率调用 `group_by_resolution`，本组先把判定和配置阈值做成可测试的策略函数；实际少数方向 GPU 重渲染需要把分段扫描元数据传入 `VideoInfo`，再在 `_check_for_merge` 派发前接入，避免在没有真实动态素材时伪造验收。
4. **验证**：合成开头不稳、短暂切换、长时间切换三种时间线；对现有固定 1600×1280 的 problem_resolution 运行一次扫描，明确记录它没有段内真实分辨率切换。

关键帧扫描实测：`7月30日22点50分_clip.flv` 的前五个关键帧均为 1600×1280，素材没有段内动态分辨率切换；因此该素材用于验证等比缩放和黑边，动态切换由合成时间线测试覆盖。
