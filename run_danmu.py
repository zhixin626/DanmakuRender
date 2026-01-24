import asyncio
import logging
import os
import sys
import requests
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

def get_douyu_gift_info(gid: int, skin_id: int = 0):
    url = "https://gift.douyucdn.cn/api/gift/v5/web/single"
    params = {
        "gid": gid,
        "skinId": skin_id,
    }

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://www.douyu.com/",
        "Origin": "https://www.douyu.com",
        "Accept": "application/json",
    }

    resp = requests.get(url, params=params, headers=headers, timeout=10)
    resp.raise_for_status()
    return resp.json()


def print_danmaku(uname, content, hex_color,type="弹幕"):
    # 1. 定义 ANSI 控制码
    RESET = "\033[0m"
    BOLD = "\033[1m"

    # 2. 处理颜色：将 Hex 字符串转换为 RGB 整数
    # 假设 hex_color 可能是 "FF0000" 或 "#FF0000"
    clean_hex = hex_color.lstrip('#')
    try:
        r = int(clean_hex[0:2], 16)
        g = int(clean_hex[2:4], 16)
        b = int(clean_hex[4:6], 16)
        # 38;2;R;G;B 是设置前景色为 TrueColor 的标准 ANSI 写法
        COLOR_CODE = f"\033[38;2;{r};{g};{b}m"
    except (ValueError, IndexError):
        # 如果颜色格式不对，回退到默认颜色
        COLOR_CODE = ""

    # 3. 输出 (替代 console.print)
    print(f"【{type}】{BOLD}{uname}{RESET}:{COLOR_CODE}{content}{RESET}")

async def main():
    # url = "https://live.bilibili.com/1852504554"
    url = "https://www.douyu.com/3125893" # 冷狗
    # url = "https://live.bilibili.com/55" # gemini
    # url = "https://www.douyu.com/4386404"
    # url = "https://www.douyu.com/5669195" # 亚瑟王
    # url = "https://live.douyu.com/96291" #东北大鹌鹑
    dm_queue = asyncio.Queue()

    # 实例化客户端
    client = DanmakuClient(url, dm_queue)

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
                print_danmaku(uname, content, color,type="弹幕")

            elif dm.dtype == 'gift':
                # pass
                uname=dm.uname
                text=dm.text
                color=dm.color
                print_danmaku(uname, text, color,type="礼物")





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

# if __name__ == '__main__':
#     from DMR.utils.utils import replace_keywords
#     from DMR.utils.danmaku import GiftDanmaku
#     kw=GiftDanmaku(
#         text="haha",
#         price=1,
#         extra="123")
#     string="{extra}"
#     print(replace_keywords(string,kw))
