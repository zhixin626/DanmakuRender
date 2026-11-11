# 1d-1：PyNvVideoCodec 码率控制

## 六项汇报

1. **改了什么**：`CreateEncoder` 现在显式传入 `rc="vbr"`、`bitrate`、`maxbitrate` 和 `vbvbufsize`；按当前 NVENC 驱动实测校准目标值。官方 API 支持这些参数和 CBR/VBR 速率控制模式。
2. **为什么这样改**：此前 GPU 两小时输出约 11.3Mbps、10.12GB，明显高于 E 的约 6.4Mbps、5.71GB；默认编码控制没有按配置生效。
3. **遇到的问题和解决办法**：直接传入 4M 的 VBR 实际约 3.42Mbps，传入 6M 实际约 5.10Mbps；最终采用配置码率 ×1.2 的内部目标和 ×1.5 的 maxbitrate，5 分钟实测 G 视频 4.089Mbps。
4. **意见和想法**：full_coverage 5 分钟 E 视频码率 4.116Mbps，G 校准后 4.089Mbps，差 0.6%，达到 ±10% 要求；校准因子是当前 PyNvVideoCodec/NVENC 驱动行为，后续换 GPU 或 SDK 版本应重新测。
5. **整体看法**：码率失控已解决，G 文件大小不会再以 1c 的 11.3Mbps 级别膨胀；下一步可按新码率重新做源视频 PSNR、YUV 均值和 CPU/GPU 遥测。
6. **给 Claude 的建议**：请审查 `gpu_render.py` 的 rc/vbv 参数和 `G_vbr5m_25fps.mp4` 的 ffprobe 结果；如果接受校准策略，再更新两小时 G 结果和最终验收总结。

## 实测

| 输出 | 视频码率 | 文件大小 |
|---|---:|---:|
| E_baseline_25fps | 4.116Mbps | 160.3MB |
| G_vbr5m_25fps | 4.089Mbps | 158.2MB |

参考：NVIDIA PyNvVideoCodec Encoder API 的 `rc`、`bitrate`、`maxbitrate`、`vbvbufsize` 参数说明。
