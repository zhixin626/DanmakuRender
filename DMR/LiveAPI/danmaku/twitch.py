import random
import re
from datetime import datetime

from DMR.utils import split_url, UserDanmaku


class Twitch:
    heartbeat = "PING"
    heartbeatInterval = 40

    @staticmethod
    async def get_ws_info(url, **kwargs):
        reg_datas = []
        room_id = split_url(url)[1]

        reg_datas.append("CAP REQ :twitch.tv/tags twitch.tv/commands twitch.tv/membership")
        reg_datas.append("PASS SCHMOOPIIE")
        nick = f"justinfan{int(8e4 * random.random() + 1e3)}"
        reg_datas.append(f"NICK {nick}")
        reg_datas.append(f"USER {nick} 8 * :{nick}")
        reg_datas.append(f"JOIN #{room_id}")

        return "wss://irc-ws.chat.twitch.tv", reg_datas

    @staticmethod
    def decode_msg(data):
        msgs = []
        if data is not None:
            for d in data.splitlines():
                try:
                    content = re.search(r"PRIVMSG [^:]+:(.+)", d).group(1)
                    name = re.search(r"display-name=([^;]+);", d).group(1)
                    # if content[0] == '@': continue # 丢掉表情符号
                    color = re.search(r"color=#([a-zA-Z0-9]{6});", d).group(1).lower()
                    msgs.append(UserDanmaku(
                        dtype="danmaku",
                        uname=name,
                        uid=None,
                        content=content,
                        text=content,
                        color=color,
                        timestamp=datetime.now().timestamp(),
                        raw=d,
                    ))
                except Exception:
                    pass
        return msgs
