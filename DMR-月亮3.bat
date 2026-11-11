@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title DMR - 月亮3
set "PYTHON=%~dp0venv\Scripts\python.exe"
if not exist "%PYTHON%" (
  echo 找不到根目录 venv，请先按 README-gpu.md 安装环境。
  pause
  exit /b 1
)
echo 此入口仅启动当前 worktree 的月亮3任务，不修改正式 DMR 配置。
echo 原任务文件保持 enable: false；确认后仅本次运行启用。
choice /C YN /N /M "启动月亮3任务（流程按配置）？[Y/N] "
if errorlevel 2 exit /b 0
"%PYTHON%" -c "from DMR.Render.gpu_render import load_cuda_modules; load_cuda_modules()"
if errorlevel 1 (
  echo GPU环境未就绪，请安装 requirements-gpu.txt。
  pause
  exit /b 1
)
REM 复用三层配置，只生成本次启动副本；WebUI使用本机5001端口。
"%PYTHON%" -c "from pathlib import Path; import yaml; from DMR.utils import merge_dict; d=Path('.temp/month-session'); d.mkdir(parents=True,exist_ok=True); task=yaml.safe_load(Path('configs/DMR-月亮3.yml').read_text(encoding='utf-8')); task['enable']=True; (d/'DMR-月亮3.yml').write_text(yaml.safe_dump(task,allow_unicode=True),encoding='utf-8'); g=yaml.safe_load(Path('configs/global.yml').read_text(encoding='utf-8')); g=merge_dict(g,{'dmr_engine_args':{'config_path':str(d/'DMR-月亮3.yml')},'webservice_kernel_args':{'host':'127.0.0.1','port':5001}}); (d/'global.yml').write_text(yaml.safe_dump(g,allow_unicode=True),encoding='utf-8')"
if errorlevel 1 (
  echo 生成启动配置失败。
  pause
  exit /b 1
)
echo WebUI: http://127.0.0.1:5001
echo 首次上传需要登录账号 b站_不可一世冷狗，按程序提示扫码。
"%PYTHON%" main.py --skip_update --config ".temp\month-session\global.yml"
pause
endlocal
