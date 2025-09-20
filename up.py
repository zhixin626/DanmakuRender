import subprocess as sp
import os
from datetime import datetime

def concat(files,out_path):
    with open("filelist.txt", "w", encoding="utf-8") as f:
        for file in files:
            f.write(f"file '{file}'\n")

    cmd = [
            'tools/ffmpeg.exe', "-y",
            "-f", "concat",
            "-loglevel", "error",
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

if __name__ == '__main__':
    up_file(r"D:\DanmakuRender\test（弹幕版）\两白一黑子-2025.09.14.10点30分（弹幕版）.mp4")
