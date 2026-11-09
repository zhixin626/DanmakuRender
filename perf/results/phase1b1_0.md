# 阶段 1b-1 (0)：代码结构整理

## 1. 改了什么

- 新增 DMR/Render/sprites.py，集中放置 emoji 字体解析、表情包加载、tokenize、pill 解析和 SpriteFactory.get_sprite。
- emoji_render_mt.py 改为从 sprites.py 导入公共绘制逻辑，保留 EmoticonStore、时间解析和渲染流程。
- 重写 DMR/Render/gpu_render.py 的结构，拆出 decode_frames、NV12Buffer、SpriteCompositor、encode_to_file、mux_with_audio 和 render_one；删除 SimpleDecoder、run_probe 和旧的 apply_kernel 路径。
- 新增 perf/run_gpu_render.py，把 CUDA wheel DLL 配置移到 perf 启动脚本。

## 2. 为什么这样改

公共精灵逻辑只保留一份，下一步 GPU 真实弹幕可以直接复用旧渲染器的字体和图片处理。GPU 模块现在按解码、缓冲区、合成、编码、封装分层，入口参数也接近 EmojiDmRender.render_one，便于后续 1b-4 接入。

## 3. 遇到的问题和解决办法

第一次运行旧 E 时发现抽取过程中误删了 check_tool、audio_codec、fmt 和 QDEPTH 等非精灵辅助函数；已全部恢复并通过重跑。GPU 模块不再在 DMR/导入时写死 perf/tmp/venv312，测试环境由 perf/run_gpu_render.py 配置。

## 4. 意见和想法

结构整理后职责边界清楚，当前 GPU 合成仍是压力方块原型，真实 RenderPlan 事件尚未接入。SpriteFactory 保持旧 get_sprite 的排版和 YUV 转换逻辑，后续应避免再复制一份 Pillow 绘制实现。

## 5. 整体看法

旧 E 重跑 normal_dense 成功，300.009 秒、1080×1920、HEVC 加 AAC 音频；与阶段 0b E 基线在 10%、50%、90% 截图的 RGB 原始像素平均差、最大差和变化像素数均为 0，说明画质和行为没有变化。结构已为低 CPU 的真实 GPU 合成铺好基础，尚未在本步骤重新测量 GPU 性能。

## 6. 给 Claude 的建议

请先审查 sprites.py 的公共接口和 gpu_render.py 的五层职责；若结构符合要求，下一步再在 SpriteCompositor 中接入 RenderPlan 图集、Y/UV kernel 和路径插值，并按阶段 0b 格式记录 CPU、GPU、NVENC/NVDEC 数据。

## 环境与验证

- CPU：Intel(R) Core(TM) i7-14700KF
- GPU：NVIDIA GeForce RTX 4070 Ti SUPER，驱动 591.86
- ffmpeg：2025-12-10-git-4f947880bd-full_build-www.gyan.dev
- 旧 E 重跑输出：perf/tmp/step0_E_after_sprites.mp4
- 对比截图：perf/results/phase1b1_0_shots/
- 诊断结果：基线与重跑输出时长均为 300.009 秒，流均为 HEVC 1080×1920 + AAC；10/50/90% 像素差均为 0。
- 已通过 py_compile；perf/run_gpu_render.py --help 可独立启动。
