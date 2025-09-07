@echo off
REM 切换到脚本所在目录
cd /d %~dp0

REM 激活虚拟环境
call venv\Scripts\activate.bat
python main.py
REM 保持当前窗口，进入交互模式
cmd /k
