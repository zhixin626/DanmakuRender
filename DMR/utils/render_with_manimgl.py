import logging,subprocess,threading
from pathlib import Path
logger=logging.getLogger(__name__)

def rendercover_with_manimgl(name, time,color,year,output_dir,is_open=False,extra_args=None,output_filename=None):
    """
    使用manimgl渲染封面，保存路径 Rf"D:\\DanmakuRender\\{name}"
    """
    manimgl_exe = r"D:\manim\venv\Scripts\manimgl.exe"
    script = "D:/zhixin_videos/_2025/danmakucover/cover.py"
    scene_name = "cover"

    cmd = [
        manimgl_exe,
        script,
        scene_name,
        name,
        time,
        color,
        year,
        "-s",
        "-w" if not is_open else "-o",
        "--video_dir",output_dir,
        "-q",
    ]
    if extra_args:
        # 确保是 list 或 tuple
        cmd += list(extra_args)

    subprocess.run(cmd, check=True)

    if output_filename and output_filename != "cover.png":
        src = Path(output_dir) / "cover.png"
        dst = Path(output_dir) / output_filename
        if src.exists():
            src.replace(dst)

def rendercover_with_manimgl_bg(name: str, time: str,color,year,output_dir:str,output_filename:str=None):
    """
    后台异步生成封面，不阻塞主线程，会打印成功日志或错误日志。
    """
    def worker():
        try:
            rendercover_with_manimgl(name, time,color,year,output_dir,output_filename=output_filename)
        except Exception as e:
            logger.exception(f"封面生成失败 name:{name},time:{time},year:{year},error:{e}")
            return
        logger.info(f"封面生成成功  name:{name}  time:{time}  year:{year}")

    t = threading.Thread(target=worker, daemon=True)
    t.start()


if __name__ == '__main__':
    pass