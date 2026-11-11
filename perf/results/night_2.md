# 第2项：少数方向分段重渲染到主画布

1. **改了什么**：LiveEvents 在所有 dm_video 分段 ready 后、整场合并前读取 resolution spans；`re_render_minority` 时把少数方向段提交到现有 Render 队列，使用 GPU 登记表中的 `RERENDER_ENGINE` 并强制主方向1080p画布。成功回调替换 dm_video、沿现有 send2trash 路径删除旧产物，再继续合并；失败/停止恢复旧产物并标记回退 parts。`_log_resolution_policy` 只执行一次并缓存决策。
2. **为什么这样改**：让短暂少数方向段最终和主方向统一画布，Merger 可自然合成一个视频；失败时保留可用原段，避免整场合并卡死。
3. **遇到的问题和解决办法**：Render 队列已有重试/忽略协议，重渲染复用同一 PipeMessage、failed_tasks 和 WebUI 路径；新增 `_resolution_rerender` 标记防止回调再次触发重渲染；修复了未齐分段时过早缓存决策、多段失败提前合并、以及 src_video 合并被 dm holding 阻塞。测试覆盖主横/少数竖、失败回退、多段等待、source 一致、src 合并不受阻塞。
4. **意见和想法**：原始录像 src_video_pre 不动；只有成功后替换 dm_video。parts 情况继续交给现有 `group_by_resolution`，不新增 Merger 特殊分支。
5. **整体看法**：138 项 CPU/结构测试通过；第2项队列派发、成功替换和失败回退均通过隔离测试。真实隔离链路已完成：横屏 problem_resolution 约10秒 + 竖屏B站片段约3秒，经 LiveEvents→现有Render队列→替换→Merger 输出一个 1920×1080、约14.0秒视频；切换前后截图在 `演示视频/2026-11-11_12_少数方向重渲染/`。
6. **给 Claude 的建议**：请审查 `_dispatch_resolution_rerender` 的失败回退、旧文件清理时机和跨段主方向判定；若需真实拼接媒体验收，建议采用横屏 problem_resolution + B站竖屏片段，主横屏1920×1080。

机器：i7-14700KF / RTX 4070 Ti SUPER；实际用时约 60 分钟。gate 放行；`verify_all.py --tests-only` 通过。
