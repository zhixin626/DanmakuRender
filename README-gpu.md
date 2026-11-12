# GPU 弹幕渲染

`gpu_render` 用 NVDEC 解码、CuPy 叠加文字/表情/面板、NVENC 编码，减少 CPU 管道拷贝。支持当前的 H.264/HEVC、8-bit NV12 路径，沿用任务的三层配置、整数倍输出帧率及合并/上传流程。

## 环境与安装

需要支持所选编码和解码格式的 NVIDIA 显卡、匹配的 NVIDIA 驱动、可运行的 FFmpeg/ffprobe。参考 [NVIDIA 系统要求](https://docs.nvidia.com/video-technologies/pynvvideocodec/read-me/latest/system-requirements-common.html)及[编解码支持矩阵](https://developer.nvidia.com/video-encode-and-decode-gpu-support-matrix-new)。本分支实测环境为 Windows x64、Python 3.12、RTX 4070 Ti SUPER、驱动 610.88；其他组合需验证。

在正式 DMR 维护窗口、使用它自己的 Python 3.12 venv 安装（不要复制开发 worktree 的 venv）。GPU 依赖已合并进 `requirements.txt`：

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements.txt
nvidia-smi
ffmpeg -version
```

`requirements.txt` 包含基础运行依赖、PyNvVideoCodec、CuPy CUDA12 和配套 CUDA runtime/NVRTC wheel；文件保持纯 ASCII，避免中文 Windows 上 pip 按 GBK 读取失败。代码从当前解释器查找 DLL，不需要写死开发机路径。请配置已安装且支持中文的字体，平台表情放在 `emoji_pack/<平台>/`，纯 emoticon 缓存统一在其 `emoticon/` 子目录。

## 启用与回退

任务配置只覆盖需要的项：

```yaml
render_args:
  overlay:
    engine: gpu_render
```

`vencoder` 及 `vencoder_args` 沿用原配置。GPU 读取其中的 `-b:v`、`-maxrate`、`-bufsize` 等参数，不使用校准倍率。已运行的 DMR 要在空闲时重启才能加载新代码。

## 相关配置

- `r2l.speed` 的单位是画布像素/秒，控制滚动弹幕的移动速度。
- `slide_right.gift.combo_window_seconds` 默认是 5 秒的滑动窗口：同一用户、同一礼物，相邻两次间隔不超过这个值就合并；连续送礼会在同一药丸上逐步更新数量，可按任务需要调整。
- `slide_left`、`slide_right` 的 `price_duration_ranges` 支持 4 个价格档位：0–30、30–50、50–100、100 元以上；档位的 `seconds` 决定药丸显示时长。
- `rescale_orientation: adaptive` 已映射为 `auto`，由整场结束时的少数方向分段重渲染策略统一到主画布；不再按每段即时切换画布。
- `fallback_fonts` 按顺序为 GPU/Pillow 缺字选择回退字体；测量和绘制共用同一条 cmap 分段链，找不到的字只警告一次并使用主字体。
- `layout_engine` 默认是 `basic`；确认安装并验证 RAQM 后可改为 `auto`/`raqm`。加载器只在活动 venv 的相对 `Library/bin`、`bin` 或 `DLLs` 目录寻找依赖，不下载或提交 DLL。可信安装来源应为 MSYS2 或 conda-forge，并在部署记录版本和 SHA256。

GPU 初始化或渲染失败时自动回退到 `emoji_dmrender`，并从同一布局检查点重编译 E 计划。用户停止不自动回退或重试；停止任务进入失败列表，可在 WebUI 中重试，成片独立保留，已释放直播组不会自动恢复合并/上传。未知引擎名直接报错。回退沿用原编码器配置：无 NVENC 的机器需要自行配置可用的软件编码器，回退不会自动改编码器。

## 只渲染已有录像

```powershell
.\venv\Scripts\python.exe render_only.py
.\venv\Scripts\python.exe render_only.py --task 月亮3 --video "D:\录像\原片.flv" --danmaku "D:\录像\原片.jsonl" --output "D:\录像\成片.mp4"
```

不带参数时按视频/弹幕→任务→输出→确认逐步选择，也支持文件夹批量。参数模式不提问；`--output test.mp4` 可使用当前目录。输出已存在时，交互模式可覆盖/改名/跳过，参数模式会报错。

代码只依赖仓库的 `DMR/`、`configs/` 和安装依赖，不需要 `perf/`、演示素材或开发机目录。维护后先用少量已录完素材验证录制→渲染→合并/上传衔接，再恢复自动任务。
