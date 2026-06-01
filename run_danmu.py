import asyncio
import logging
import os
import sys
# 强制禁用代理，防止干扰 SSL 握手
os.environ['http_proxy'] = ''
os.environ['https_proxy'] = ''

# 确保能找到 DMR 文件夹
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# 按照你的要求修改导入路径
from DMR.LiveAPI.danmaku import DanmakuClient
from DMR.utils import SimpleDanmaku
from datetime import datetime

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


RESET  = "\033[0m"
GREEN  = "\033[32m"
YELLOW = "\033[33m"
RED    = "\033[31m"
BLUE   = "\033[34m"

async def main():
    # url = "https://live.bilibili.com/1852504554"
    # url = "https://www.douyu.com/3125893" # 冷狗
    # url = "https://live.douyu.com/793400" # gemini
    # url = "https://www.douyu.com/4386404"
    # url = "https://www.douyu.com/5669195" # 亚瑟王
    # url = "https://live.douyu.com/96291" #东北大鹌鹑
    # url="https://live.bilibili.com/24486091"#月亮3
    # url="https://live.douyin.com/zcw199608" # zwc
    url="https://live.bilibili.com/22230707" # zwc
    dm_queue = asyncio.Queue()
    douyin_dm_cookies=R"D:\DanmakuRender\.login_info\b站_线代不抽象.json"

    # 实例化客户端
    # client = DanmakuClient(url, dm_queue,bilibili_dm_cookie_path='.login_info/bili_watch_cookies.json')
    client = DanmakuClient(url, dm_queue,douyin_dm_cookies=douyin_dm_cookies)

    print(f"[*] 正在初始化并连接至: {url}")

    client_task = None
    try:
        # 启动客户端后台任务
        client_task    = asyncio.create_task(client.start())


        print("[*] 监听已启动！按下 Ctrl+C 可停止测试。\n")

        while True:
            # 1. 获取弹幕字典
            dm = await dm_queue.get()
            if not isinstance(dm, SimpleDanmaku):
                dm = SimpleDanmaku(
                    dtype=dm.get('msg_type', 'other'),
                    uname=dm.get('name', ''),
                    content=dm.get('content', ''),
                    timestamp=dm.get('timestamp', datetime.now().timestamp()),
                    color=dm.get('color', 'ffffff'),
                )

            if dm.dtype == 'danmaku':
                # pass
                uname = dm.uname
                content = dm.content
                color=dm.color
                print(f"[danmu]{content}")
                # if "巧丽" in uname or "学会自己爬" in uname or "有点抽象" in uname:
                #     print(f"【弹幕】【{uname}】{content}")
                #     print(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

            elif dm.dtype == 'gift':
                # pass
                uname=dm.uname
                text=dm.text
                color=dm.color
                print(f"{BLUE}[gift]{RESET}{text}")
                # if "巧丽" in uname or "学会自己爬" in uname or "有点抽象" in uname:
                #     print(f"{YELLOW}【礼物】【{uname}】{RESET}{text}")
                #     print(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

            elif dm.dtype == 'member':
                text=dm.text
                print(f"{RED}【会员】{RESET}{text}")

            elif dm.dtype == 'superchat':
                name=dm.uname
                price=dm.price
                price_unit=dm.price_unit
                duration=dm.duration
                content=dm.content
                color=dm.color
                print(f"{RED}【superchat】{RESET}{name} {price} {price_unit} {duration} {content} {color}")
                print(dm["raw"])






    except asyncio.CancelledError:
        pass
    except Exception as e:
        logging.error(f"发生错误: {e}")
    finally:
        print("\n[*] 正在清理并关闭连接...")
        await client.stop()
        if client_task:
            client_task.cancel()



        print("[*] 测试已结束。")

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

