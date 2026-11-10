# NVIDIA GPU 渲染

本轮验证环境：Python 3.12、仓库根目录 `venv/`。`DMR/` 和离线渲染入口不依赖 `perf/`。

```powershell
python -m venv venv
venv\Scripts\python.exe -m pip install -r requirements-gpu.txt
venv\Scripts\python.exe -m pytest tests -q
```

已有完整环境无需重建。`requirements-gpu.txt` 包含常规 DMR 依赖以及可选 NVIDIA/CUDA 和测试依赖。引擎读取现有 `vencoder_args` 的 `-b:v`、`-maxrate`、`-bufsize`；未配置项使用编码器默认值。

单文件渲染：

```powershell
venv\Scripts\python.exe render_only.py --task 月亮3 --video 输入.flv --danmaku 输入.jsonl --output 输出.mp4
```

`--task` 从 `configs/` 读取任务，按 default → global → task 合并渲染参数。任务即使停用也可手工离线渲染，不会启动录制或上传。四个参数必须同时给出；已有输出不会覆盖。批量交互模式继续保留。

「月亮3」的录制、GPU 渲染和上传入口为根目录 **DMR-月亮3.bat**。双击并确认 Y 后，只为本次启动生成 `.temp/month-session/` 配置副本，启用月亮3任务；原 `configs/DMR-月亮3.yml` 仍保持停用，全局文件不变。此入口只运行当前目录中的 DMR，WebUI 地址为 `http://127.0.0.1:5001`。不要同时在两个 DMR 实例中录制同一房间。

配置沿用既有 B 站账号 `b站_不可一世冷狗`，房间为 `24486091`。账号凭据位于当前仓库 `.login_info/`；未登录时首次上传按程序提示扫码。配置文件只记录凭据路径，不包含凭据内容。首次验收保留原始录像和成片，未启用自动清理。

当前月亮3配置按用户手工设置保留 `auto_upload: false`、VIP名单和 `is_only_self: 1`。启动时自动录制与渲染；已配置好上传参数，但不会自动上传，上传开关由用户控制。

GPU 渲染通过百分比回调更新现有 WebUI 进度；`GpuRender.stop()` 可中止图集准备、逐帧编码及封装等待。未完成文件放在本任务临时目录，停止后清理，既有成片不受影响。用户停止不会触发旧引擎回退。
