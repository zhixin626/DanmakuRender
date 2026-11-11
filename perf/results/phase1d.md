# 阶段 1d：闪烁与面板字号修复

## 六项汇报

1. **改了什么**：在 `gpu_render.py` 每次 `Encode()` 前调用 `cupy.cuda.Stream.null.synchronize()`；`SpriteFactory` 增加共享 Pillow 图像接口，`panel_sprite` 改为调用 `get_sprite_image`，自动使用 `SCALE=0.75`、节点颜色和描边；新增 `perf/check_flicker.py`。
2. **为什么这样改**：修复 NV12 合成异步导致的偶发弹幕层闪烁，并让 SC/礼物/会员面板字号、基线、Emoji 和描边与普通弹幕共用同一套 SpriteFactory 规则。
3. **遇到的问题和解决办法**：此前 panel_sprite 重新实现 tokenize，字号直接使用 30/60，导致 GPU 面板约放大 1.33 倍；现在由 SpriteFactory 统一渲染。`max_frames` 的 CFR 尾部补帧也一并修正，避免短测试意外跑完整素材。
4. **意见和想法**：v2 60 秒 GPU 输出通过 py_compile 和渲染；`check_flicker.py` 在 20–35 秒 31 个预期弹幕时刻检查，闪烁帧为 0；v2 截图显示面板字号和描边明显收敛。
5. **整体看法**：闪烁问题已通过同步点解决，面板绘制现在复用 SpriteFactory，后续可在统一码率和源视频 PSNR 验证中继续检查。
6. **给 Claude 的建议**：请审查 `perf/results/phase1b2_v2_shots/G_v2_30.png`、`perf/check_flicker.py` 和 `演示视频/02/03/04 ... _v2.mp4`；若目视确认通过，再继续 1d 的码率控制和源视频画质对比。

## 验证

- `G_v2_25fps.mp4`：1500 帧、25fps、约 60 秒。
- `G_variant_v2_25fps.mp4`：配置 B 1500 帧。
- `G_cfr50_v2.mp4`：1500 帧、50fps，max_frames 不再被尾部补帧绕过。
- `check_flicker.py`：`checked=31, flicker_frames=0`。
- gh 评论测试：短评论和 body-file 长评论均成功，后续汇报可用 gh。
