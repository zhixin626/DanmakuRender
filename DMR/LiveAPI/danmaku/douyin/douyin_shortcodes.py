# -*- coding: utf-8 -*-
# douyin_shortcodes.py
import re
import logging

logger = logging.getLogger(__name__)

# 映射：短码 -> emoji（若没映射就保持原样）
EMOJI_MAP = {
    "微笑":"🙂", 
    "害羞":"😊",
    "爱心":"❤",
    "便便":"💩",
    "惊讶":"😲",
    "不看":"🙈",
    "囧"  :"🙄",
    "赞"  :"👍",
    "看"  :"🐶",
    "胜利":"✌",
    "流泪":"😢",
    "呲牙":"😁",
    "尬笑":"😅",
    "大哭":"😭",
    "OK"  :"👌",
    "发怒":"😡",
    "亲亲":"😚",
    "笑哭":"😂",
    "捂脸":"🤦",
    "比心":"🤞", 
    "调皮":"😛",
    "心碎":"💔",
    "":"",
    "":"",
}

pattern = re.compile(r"\[([^\[\]]+)\]")  # 匹配：[短码] 能正确识别[[短码]]

def replace_shortcodes_to_emoji(text: str) -> str:
    if text is None or  text == '':
        return text
    def _repl_keep(m):
        name = m.group(1)
        emoji = EMOJI_MAP.get(name)
        return emoji if emoji else m.group(0)

    out = pattern.sub(_repl_keep, text)

    return out
