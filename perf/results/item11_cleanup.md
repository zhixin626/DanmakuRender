## 第 11 项：代码清理

1. **改了什么**：删除 `liveevents.py` 未使用的 `re`、`LiveAPI`、`ENGINES` 导入；删除 `Render/__init__.py` 未使用的 `join`；将 `SpriteCompositor.apply` 与 `_apply_plan` 合并；`decode_frames` 改由调用方传入 CuPy；把音画时长容差、回退 run-cache 上限和 metadata itemsize 写成命名常量；同步更新测试和缩放回归脚本。
2. **为什么这样改**：去掉无效分层和隐含模块加载，明确 GPU 依赖边界，避免散落的字面常量。
3. **遇到的问题和解决办法**：旧版缩放回归脚本还会动态加载优化前模块，因此增加兼容调用适配器，只对旧模块保留旧签名；没有改配置文件或生产目录。
4. **意见和想法**：`decode_frames` 现在只负责解码和持有缓冲，CuPy 生命周期由 `render_one` 管理；metadata pinned 分配使用 `np.dtype(np.int32).itemsize`，不改变布局。
5. **整体看法**：第 11 项完成。完整 CPU 回归 `159 passed`；GPU 相关定向回归 `42 passed`；`git diff --check` 通过。
6. **给 Claude 的建议/决策**：pyflakes 已安装到隔离 venv 并执行；剩余提示来自原有 `uuid` 循环变量遮蔽和 `liveevents.py` 的既有星号导入，未由本项引入。若要把这些历史警告也纳入本项，请在评论中指定，否则继续第 7 项第二轮拆分。

提交：4141e75
实际用时：约 18 分钟。
