# bark_notifier.py
import os
import time
import threading
import requests
from urllib.parse import quote
from dotenv import load_dotenv
from DMR.LiveAPI import LiveAPI
import logging
logger = logging.getLogger(__name__)

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".login_info", ".env"))
_BARK_KEY = os.getenv("BARK_KEY")


_VALID_SOUNDS = {"birdsong", "glass","healthnotification", "silence"}


def _send_bark(bark_key, title, content, face_url=None, platform=None, sound="slience"):
    encoded_title = quote(title, safe='')
    encoded_body  = quote(content, safe='')

    if platform == "douyu":
        jump_url = "douyutv://"
    elif platform == "douyin":
        jump_url = "snssdk1128://"
    elif platform == "bilibili":
        jump_url = "bilibili://"
    else:
        jump_url = None

    params = []
    if face_url:
        params.append(f"icon={quote(face_url, safe='')}")
    if jump_url:
        params.append(f"url={quote(jump_url, safe='')}")
    if sound:
        params.append(f"sound={sound}")

    query = "&".join(params)
    bark_url = f"https://api.day.app/{bark_key}/{encoded_title}/{encoded_body}?{query}"

    res = requests.get(bark_url, timeout=10)
    res.raise_for_status()


def bark_notify_url(room_url: str, sound: str = "birdsong"):
    """非阻塞调用，内部自动 3 次 retry"""
    def _notify_worker(room_url: str, sound: str, max_retries: int = 3):
        for attempt in range(1, max_retries + 1):
            try:
                api = LiveAPI(room_url)
                streamer_info = api.GetStreamerInfo()
                room_info     = api.GetRoomInfo()
                _send_bark(
                    bark_key = _BARK_KEY,
                    title    = f"【{streamer_info['name']}】开播提醒",
                    content  = room_info["title"],
                    face_url = streamer_info["face_url"],
                    platform = streamer_info["platform"],
                    sound    = sound,
                )
                logger.info(f"✅ Bark 推送成功：{streamer_info['name']} 开播")
                return
            except Exception as e:
                logger.info(f"⚠️  Bark 推送失败（第 {attempt}/{max_retries} 次）：{e}")
                if attempt < max_retries:
                    time.sleep(2 ** attempt)  # 指数退避：2s, 4s
        logger.info("❌ Bark 推送达到最大重试次数，放弃。")
    t = threading.Thread(
        target=_notify_worker,
        args=(room_url, sound),
        daemon=True
    )
    t.start()


def bark_notify(title, content, face_url=None, platform=None, sound="birdsong"):
    t = threading.Thread(
        target=_send_bark,
        args=(_BARK_KEY, title, content),
        kwargs={"face_url": face_url, "platform": platform, "sound": sound},
        daemon=True
    )
    t.start()