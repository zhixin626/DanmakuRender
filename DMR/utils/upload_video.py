import subprocess as sp
from datetime import datetime
import logging
import os
import re
import tempfile
import time
import json
logger = logging.getLogger(__name__)

def upload_zuozuo_video(
    file_path: str,
    stime: datetime,
    etime: datetime,
    duration: str,
    cover_path:str,
    is_only_self=True,
    account: str = "3546637425182939",   # 账号ID，可按需改
    biliuprs_path: str = r"D:\DanmakuRender\tools\biliup.exe",
    max_retry: int = 3,
    retry_interval: int = 6,            # 每次失败后休眠秒数
):
    """
    使用 biliup.exe 上传佐佐视频：
    - 自动拼好 desc / title
    - 解析输出拿到 bvid
    - 失败时自动重试 max_retry 次
    :return: (success: bool, bvid: str, log: str)
    """

    # 1. 先做一些存在性检查
    if not os.path.exists(biliuprs_path):
        raise FileNotFoundError(f"biliup.exe 不存在: {biliuprs_path}")
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"要上传的视频文件不存在: {file_path}")

    # 2. 基本参数拼装
    cookies_path = f".login_info/{account}.json"

    desc = (
        f"开播时间: {stime.year}年{stime.month}月{stime.day}日"
        f"{stime.hour}时{stime.minute}分\n"
        f"下播时间: {etime.year}年{etime.month}月{etime.day}日"
        f"{etime.hour}时{etime.minute}分\n"
        f"直播时长: {duration}\n"
    )
    title = f"【佐佐酱】{stime.month}月{stime.day}日"

    extra_fields = {
    "watermark": {"state": 0},
    "is_only_self": 1 if is_only_self else 0,
    "no_disturbance": 1,
    }

    base_cmd = [
        biliuprs_path,
        "-u", cookies_path,
        "upload",
        "--copyright", "1",
        "--desc", desc,
        "--dtime", "0",
        "--limit", "3",
        "--no-reprint", "1",
        "--open-elec", "1",
        "--source", "",
        "--tag", "佐佐酱",
        "--tid", "65",
        "--title", title,
        "--cover",cover_path,
        "--extra-fields",json.dumps(extra_fields, ensure_ascii=False),
        file_path,
    ]

    # 3. 内部函数：跑一次 biliup，并解析 bvid
    def _run_once():
        """
        跑一次 biliup.exe，返回 (success: bool, bvid: str, log: str)
        成功的定义：输出里解析到了 BV 号
        """
        # 用临时文件接 bilip.exe 的 stdout，方便之后解析
        with tempfile.TemporaryFile(dir=".temp") as logfile:
            os.makedirs(".temp", exist_ok=True)

            # logger.info(f"开始上传: {file_path}")
            # logger.debug(f"biliup 命令: {base_cmd}")

            proc = sp.Popen(
                base_cmd,
                stdin=sp.PIPE,
                stdout=logfile,
                stderr=sp.STDOUT,
                bufsize=10**8,
            )
            proc.wait()

            logfile.seek(0)
            log_lines = logfile.readlines()

        log_text = ""
        out_bvid = None

        for raw in log_lines:
            line = raw.decode("utf-8", errors="ignore").strip()
            log_text += line + "\n"
            if '"bvid"' in line:
                # 和你项目里的 biliuprs.upload_once 一样的正则
                m = re.search(r"(BV[0-9A-Za-z]{10})", line)
                if m:
                    out_bvid = m[0]

        if out_bvid:
            # logger.info(f"上传成功，bvid = {out_bvid}")
            return True, out_bvid, log_text
        else:
            logger.warning("本次上传未解析到 bvid，视为失败。")
            return False, "", log_text

    # 4. 重试逻辑
    last_log = ""
    for attempt in range(1, max_retry + 1):
        logger.info(f"第 {attempt}/{max_retry} 次尝试上传佐佐视频...")
        ok, bvid, log_text = _run_once()
        last_log = log_text

        if ok:
            # 成功就直接返回
            return True, bvid, log_text

        if attempt < max_retry:
            logger.warning(
                f"第 {attempt} 次上传失败，{retry_interval} 秒后重试..."
            )
            time.sleep(retry_interval)

    # 如果走到这里，说明多次重试都没拿到 bvid
    logger.error("多次重试仍然上传失败，请检查 biliup 日志。")
    return False, "", last_log






