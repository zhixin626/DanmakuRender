import time
import queue
import sys
from DMR.Downloader.stream_downloader import StreamDownloadTask
from DMR.Config import Config

msg_queue = queue.Queue()

# taskname = "少年不太冷"
taskname = "抖音测试"

# 1) 复用项目现有 Config：读取 global + 单任务配置
config = Config(
    global_config_path="configs_test/global.yml",
    replay_config_path=[f"configs_test/DMR-{taskname}.yml"],  # 这里换成你的 testconfig 文件
)

# 2) 拿到这个任务名对应的配置
replay_cfg = config.get_replay_config(taskname)
download_args = replay_cfg["download_args"]

download_args.setdefault("taskname", taskname)
download_args["output_dir"]="./test文件"
download_args["engine"]="ffmpeg"
downloader_task = StreamDownloadTask(send_queue=msg_queue, **download_args)

if __name__ == "__main__":
    print(f"任务 [{download_args['taskname']}] 启动中...")
    print(f"监控地址: {download_args['url']}")
    print(">>> 提示：按 Ctrl+C 停止 <<<")
    # print(download_args)

    record_thread = downloader_task.start()

    try:
        while record_thread.is_alive():
            time.sleep(1)
            while not msg_queue.empty():
                msg = msg_queue.get()
    except KeyboardInterrupt:
        print("\n[!] 捕获到 Ctrl+C，正在停止程序...")
        downloader_task.loop = False
        downloader_task.stop()
        sys.exit(0)
