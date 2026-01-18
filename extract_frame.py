import subprocess
from pathlib import Path
from upload_only import strip_quotes

def main():
    video_path = strip_quotes(input("请输入需要上传的视频路径：\n").strip())
    sec = input("请输入秒数（如 400）: ").strip()

    video = Path(video_path)
    if not video.exists():
        print("❌ 视频文件不存在")
        return

    try:
        sec = float(sec)
    except ValueError:
        print("❌ 秒数必须是数字")
        return

    # 输出图片路径（同目录，同名.jpg）
    out_img = video.with_suffix(".jpg")

    cmd = [
        "ffmpeg",
        "-ss", str(sec),
        "-i", str(video),
        "-frames:v", "1",
        "-y",               # 自动覆盖
        str(out_img)
    ]

    try:
        subprocess.run(cmd, check=True)
        print(f"✅ 已生成图片: {out_img}")
    except subprocess.CalledProcessError:
        print("❌ ffmpeg 执行失败，请确认 ffmpeg 已加入 PATH")

if __name__ == "__main__":
    main()
