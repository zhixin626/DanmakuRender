import subprocess as sp
from pathlib import Path
folder=Path("D:/DanmakuRender/Tasks文件/不可一世杀手视频补档/不可一世")
for file in folder.glob("*.mp4"):
    cmd=[
    "C:/Program Files/mpv/mpv.exe",
    "--start=03:29:26",
    "--pause",
    file
    ]
    sp.run(cmd)


