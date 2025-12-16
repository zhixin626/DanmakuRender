import logging,subprocess,threading
from pathlib import Path
logger=logging.getLogger(__name__)

def rendercover_with_manimgl(name, time,color,year,output_dir,is_open=False,extra_args=None):
    """
    使用manimgl渲染封面，保存路径 Rf"D:\\DanmakuRender\\{name}"
    """
    manimgl_exe = r"D:\manim\venv\Scripts\manimgl.exe"
    script = R"D:\DanmakuRender\DMR\utils\get_cover.py"
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

def rendercover_with_manimgl_bg(name: str, time: str,color,year,output_dir:str,debug=False):
    """
    后台异步生成封面，不阻塞主线程，会打印成功日志或错误日志。
    """
    def worker():
        try:
            rendercover_with_manimgl(name, time,color,year,output_dir)
        except Exception as e:
            logger.exception(f"封面生成失败:name={name},time={time},error={e}")
            if debug:
                print(f"封面生成失败:name={name},time={time},error={e}")
            return
        logger.info(f"封面生成成功:name={name},time={time}")
        if debug:
            print(f"封面生成成功:name={name},time={time}")
    # 后台线程
    t = threading.Thread(target=worker, daemon=True)
    t.start()

def render_zuozuovideo_with_manimgl(output_dir):
    manimgl_exe = r"D:\manim\venv\Scripts\manimgl.exe"
    script = r"D:\DanmakuRender\DMR\utils\get_zuozuo_video.py"
    scene_name = "zuozuo_video"
    cmd = [
        manimgl_exe,
        script,
        scene_name,
        "-w",
        "-c","#000000",
        "--video_dir",output_dir,
        "--fps","30",
        "-q",
        "--hd",
    ]
    subprocess.run(cmd, check=True)
    return Path(output_dir)/(scene_name+".mp4")

def render_zuozuovideo_with_manimgl_bg(output_dir,debug=False):
    def worker():
        try:
            render_zuozuovideo_with_manimgl(output_dir)
        except Exception as e:
            logger.exception(f"佐佐视频生成失败：error={e}")
            if debug:
                print(f"佐佐视频生成失败：error={e}")
            return
        logger.info(f"佐佐视频生成成功")
        if debug:
            print(f"佐佐视频生成成功")
    # 后台线程
    t = threading.Thread(target=worker, daemon=True)
    t.start()

if __name__ == '__main__':
    # render_zuozuovideo_with_manimgl()
    pass