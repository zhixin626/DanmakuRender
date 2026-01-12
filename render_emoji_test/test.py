import subprocess as sp
import os


# 输入文件路径 (保持您提供的路径)
flvfile = "D:/DanmakuRender/Tasks文件/b站测试（弹幕版）/test/1.flv"
assfile = "D:/DanmakuRender/Tasks文件/b站测试（弹幕版）/test/1.ass"
output = "D:/DanmakuRender/Tasks文件/b站测试（弹幕版）/test/test.mp4"

# 【重要】请修改这里：存放 NotoColorEmoji.ttf 的文件夹路径
# 建议把字体文件单独放在一个文件夹里，例如 D:/DanmakuRender/fonts
font_dir = "D:/DanmakuRender/Tasks文件/b站测试（弹幕版）/test"

# ===========================================

def escape_filter_path(path):
    path = os.path.abspath(path).replace('\\', '/')
    path = path.replace(':', '\\:')
    return path

# 确保字体目录存在
if not os.path.exists(font_dir):
    print(f"Error: 字体目录不存在 -> {font_dir}")
    print("请确保 NotoColorEmoji.ttf 放在该文件夹下")
    exit(1)

# 处理路径转义
ass_escaped = escape_filter_path(assfile)
font_dir_escaped = escape_filter_path(font_dir)

# 构建 subtitles 滤镜字符串
# 语法: subtitles=filename='...':fontsdir='...'
vf_string = f"subtitles=filename='{ass_escaped}':fontsdir='{font_dir_escaped}'"

# 构建命令
cmd = [
    "ffmpeg",
    '-y',                      # 覆盖输出
    '-hwaccel', 'auto',        # 硬件加速解码
    '-fflags', '+discardcorrupt+genpts',
    '-analyzeduration', '2147483647',
    '-probesize', '2147483647',
    '-i', flvfile,
    '-filter_complex', vf_string, # 核心滤镜
    '-c:v', 'h264_nvenc',      # 显卡编码
    '-b:v', '6M',              # 码率
    '-c:a', 'aac',
    '-b:a', '128K',
    '-noautoscale',            # 保持比例
    output
]

print("Executing command:")
print(" ".join(cmd))
print("-" * 50)

try:
    # 执行命令
    sp.run(cmd, check=True)
    print(f"\n渲染成功！输出文件: {output}")
except sp.CalledProcessError as e:
    print(f"\n渲染失败，错误码: {e.returncode}")
