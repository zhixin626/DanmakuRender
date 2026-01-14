import time
import queue
import sys
import os
from DMR.Downloader.stream_downloader import StreamDownloadTask
# 1. 初始化消息队列 (Message Queue)
msg_queue = queue.Queue()

# 2. 准备初始化参数
# 这里的参数是根据 "DMR-不可一世杀手.yml" 的覆盖值和 "global.yml" 的默认值合并得出的
task_args = {
    "url": 'https://www.douyu.com/3125893',
    "taskname": 'test',
    "output_dir": './test',
    "output_name": '手{CTIME.MONTH}月{CTIME.DAY}日{CTIME.HOUR:02d}点{CTIME.MINUTE:02d}分',
    "segment": 7200,              # 2小时分段
    "engine": 'ffmpeg',      # 任务指定的录制引擎
    "output_format": 'flv',
    "danmaku": True,
    "video": True,
    "stop_wait_time": 3,          # 延迟下播计时（分钟）

    # 直播流选项 (来自 global.yml)
    "stream_option": {
        "stream_cdn": None,
        "stream_type": None,
        "bili_watch_cookies": ".login_info/bili_watch_cookies.json",
        "huya_mobile_api": False,
    },

    # 高级视频录制参数 (Advanced Video Args)
    "advanced_video_args": {
        "default_resolution": [1920, 1080],
        "start_check_interval": 60,
        "stop_check_interval": 60,
        "restart_interval": [0, 1, 10],   # 任务配置的重启间隔
        "max_fn_length": 80,
        "min_video_size": 1,
        "min_video_duration": None,
        "group_id": None,
        "bili_force_origin": True,
        "ffmpeg_stream_args": ['-rw_timeout','10000000','-analyzeduration','15000000','-probesize','50000000','-thread_queue_size', '16'],
        "ffmpeg_output_args": ['-movflags','faststart+frag_keyframe+empty_moov'],
        "disable_lowspeed_interrupt": False,
    },

    # 高级弹幕录制参数 (Advanced DM Args - 来自 global.yml)
    "advanced_dm_args": {
        "dm_delay_fixed": 6,
        "dm_auto_restart": 300,
        "dm_extra_inputs": [],
        "dm_file_min_time": 30
    },

    # 弹幕样式与过滤参数 (这些会通过 **kwargs 传入 DanmakuDownloader)
    "dm_format": "ass",
    "margin_h": 10,
    "margin_w": 12,
    "dmrate": 0.3,
    "font": "Microsoft YaHei",
    "fontsize": 60,
    "dst": 10,
    "dmduration": 16,
    "opacity": 0.78,
    "auto_fontsize": False,
    "outlinecolor": "000000",
    "outlinesize": 1.0,

    # 弹幕过滤规则 (Fixed: 确保 keywords 不为 None 以防止之前遇到的 Bug)
    "dm_filter": {
        "dm_type": ["danmaku", "gift"],
        "keywords": [],               # 设置为空列表而非 None
        "username": None,
        "max_length": 60
    },

    # 弹幕模板 (针对礼物消息进行了自定义)
    "dm_template": {
        # "ass_text": "{uname}:{content}",
        "danmaku": None,
        "superchat": None,
        "gift": '{uname} 送给主播价值{gift_price}{price_unit}的{gift_name}×{gift_count}',
        "entry": None
    },

}

# 3. 实例化任务
downloader_task = StreamDownloadTask(send_queue=msg_queue, **task_args)




if __name__ == "__main__":
    print(f"任务 [{task_args['taskname']}] 启动中...")
    print(f"监控地址: {task_args['url']}")
    print(">>> 提示：按 Ctrl+C 一次进行优雅停止，连按两次强制退出 <<<")

    # 启动录制线程
    record_thread = downloader_task.start()

    try:
        # 使用循环检查线程状态，而不是简单的 join()
        # 这样主线程可以持续响应 KeyboardInterrupt
        while record_thread.is_alive():
            # 每秒轮询一次，降低 CPU 占用
            time.sleep(1)

            # 可选：在这里处理 msg_queue 里的消息 (如打印日志)
            while not msg_queue.empty():
                msg = msg_queue.get()
                # print(f"[{msg.source}] {msg.msg}")

    except KeyboardInterrupt:
        print("\n[!] 捕获到 Ctrl+C，正在执行优雅停止程序...")

        # 关键步骤：修改内部循环标志位
        downloader_task.loop = False  # 停止 start_helper 的 while 循环
        downloader_task.stop()        # 停止当前的录制器和弹幕器

        # 给程序一点时间保存文件和关闭引擎 (ffmpeg/streamgears)
        timeout = 5
        start_wait = time.time()
        while record_thread.is_alive() and (time.time() - start_wait < timeout):
            print(f"正在等待后台线程退出... ({int(timeout - (time.time() - start_wait))}s)")
            time.sleep(1)

        if record_thread.is_alive():
            print("[!] 后台线程未能在规定时间内退出，强制结束进程。")
        else:
            print("[√] 录制已安全停止。")

        # 彻底退出，确保不留下僵尸进程
        sys.exit(0)