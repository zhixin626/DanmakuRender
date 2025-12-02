import subprocess as sp
import os
from datetime import datetime
from pathlib import Path
import re
from pprint import pprint
from send2trash import send2trash
import requests
import json
from functools import reduce
from hashlib import md5
import logging
import requests
import sys
sys.path.append(str(Path(r"D:\DanmakuRender\DMR\Task")))
from merge_mp4 import *
from DMR.utils import *

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

def concat(files,out_path):
    with open("filelist.txt", "w", encoding="utf-8") as f:
        for file in files:
            f.write(f"file '{file}'\n")

    cmd = [
            'tools/ffmpeg.exe', "-y",
            "-f", "concat",
            # "-loglevel", "error",
            "-safe", "0",
            "-i", "filelist.txt",
            "-c", "copy",
            "-movflags", "+faststart",
            out_path,
            ]
    sp.run(cmd)

def up_file(file_path,task_name="不可一世杀手",url='https://live.douyin.com/zcw199608'):
    current_time=datetime.now()
    year = current_time.year
    month = current_time.month
    day = 6
    biliuprs=[
    'tools/biliup.exe',
    '-u','.login_info/3546637425182939.json',
    'upload',
    '--copyright','1',
    '--cover','',
    '--desc',
    f'{task_name} 的直播回放\n标题：无我之境 \n时间：{year}年{month}月{day}日\n直播地址：{url} \n录制工具：https://github.com/SmallPeaches/DanmakuRender',
    '--dtime','0',
    '--dynamic',
    f'松弟们!手儿{year}年{month}月{day}日的直播回放来了!',
    '--limit','3',
    '--no-reprint','1',
    '--open-elec','1',
    '--source','',
    '--tag',f'直播回放,{task_name}',
    '--tid','65',
    '--title',f'不可一世杀手直播回放{year}.{month}.{day}(大弹幕版)',
    '--extra-fields','{ "watermark":{"state":1} }',
    file_path
]
    sp.run(biliuprs)


if __name__ == "__main__":
    from DMR.utils.utils import rendercover_with_manimgl

    rendercover_with_manimgl("少年不太冷", "11月2日")





