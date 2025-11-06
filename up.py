import subprocess as sp
import os
from datetime import datetime
from pathlib import Path
import re
from pprint import pprint
from send2trash import send2trash

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
    day = current_time.day
    biliuprs=[
    'tools/biliup.exe',
    '-u','.login_info/3546637425182939.json',
    'upload',
    '--copyright','1',
    '--cover','',
    '--desc',
    f'{task_name} 的直播回放\n标题：无我之境 \n时间：{year}年{month}月{day}日\n直播地址：{url} \n——————————————————————————————————\n由DanmakuRender录制：\nhttps://github.com/SmallPeaches/DanmakuRender\n',
    '--dtime','0',
    '--dynamic',
    f'哈喽，大家好！今天给大家带来{task_name} {year}.{month}.{day}的直播回放,感谢观看！由于弹幕敏感审核未通过，导致今天上传晚了，请别发敏感词了松弟门！',
    '--limit','3',
    '--no-reprint','1',
    '--open-elec','1',
    '--source','',
    '--tag',f'直播回放,{task_name}',
    '--tid','65',
    '--title',f'不可一世杀手直播回放{year}.{month}.{day}(大弹幕版)',
    '--extra-fields','{"is_only_self":1}',
    file_path
]
    print(biliuprs)
    sp.run(biliuprs)

# files=[
# 'D:\\DanmakuRender\\不可一世杀手（弹幕版）\\1.mp4',
# 'D:\\DanmakuRender\\不可一世杀手（弹幕版）\\不可一世杀手-2025.08.29.01点18分（弹幕版）.mp4'
# ]
# out_path='D:\\DanmakuRender\\不可一世杀手（弹幕版）\\final.mp4'
# concat(files,out_path)
def detect(file):
    cmd = [
            'tools/ffmpeg.exe',
            '-hide_banner',
            '-i',file,
            '-vn',
            '-af','volumedetect',
            '-f','null','-'
            ]
    r = sp.run(cmd, text=True, capture_output=True ,encoding='utf-8')
    m = re.search(r'max_volume:\s*([-\d\.]+)\s*dB', r.stderr)
    if not m:
        raise RuntimeError("未检测到 max_volume（可能没有音轨或滤镜未运行）")
    return float(m.group(1))
def amplify_to_minus1db(file):
    peak = detect(file)
    if peak is None:
        raise RuntimeError("未检测到 max_volume")

    gain_db = -1 - peak
    if gain_db < 0:
        gain_db = 0
    out = Path(file).with_name(Path(file).stem + "_amplified.mp4")
    cmd = [
        'tools/ffmpeg.exe',
        '-i', file,
        '-af', f'volume={gain_db:.2f}dB,alimiter=limit=-1dB',
        '-c:v', 'copy',
        str(out)
    ]
    sp.run(cmd)
    print(f"检测峰值 {peak:.2f} dBFS → 放大 {gain_db:.2f} dB → 输出: {out}")

if __name__ == '__main__':
    f1=r"D:\DanmakuRender\不可一世杀手（弹幕版）\11月3日声音小3.mp4"
    f2=r"D:\DanmakuRender\不可一世杀手（弹幕版）\11月4日声音小.mp4"
    f3=r"D:\DanmakuRender\不可一世杀手（弹幕版）\11月6日.mp4"
    # p=Path(f1)
    Path(f1).rename(r"D:\DanmakuRender\不可一世杀手（弹幕版）\11月3日声音小.mp4")
    # print(p)


