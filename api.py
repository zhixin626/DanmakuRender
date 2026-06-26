from fastapi import FastAPI
from DMR.utils.bili_season import sync_section_episode_titles
from pydantic import BaseModel
from fastapi.responses import PlainTextResponse
from fastapi import HTTPException
from typing import Union
import os
import requests
import json
# uvicorn api:app --reload --host 0.0.0.0 --port 8000
# uvicorn api:app --host 0.0.0.0 --port 8000

app = FastAPI()

@app.get("/")
def home():
    return {"msg": "hello from fastapi!"}

@app.get("/ping")
def ping():
    return {"pong": True}

class SyncReq(BaseModel):
    account:str
    season_id:int

@app.post("/synctitle")
def syn_title(req: SyncReq):
    result = sync_section_episode_titles(req.account, req.season_id)
    lines: list[str] = []

    # 顶部 message（比如“本次检测到 xx 个分P 标题不一致”之类）
    if result.get("message"):
        lines.append(result["message"])
        lines.append("")  # 空行分隔

    changed = result.get("changed") or []
    errors = result.get("errors") or []

    # 成功的部分
    if changed:
        lines.append(f"✅ 本次已成功同步 {len(changed)} 个分P 标题：")
        lines.append("")  # 空行

        for idx, c in enumerate(changed, start=1):
            old_title = c.get("old_title", "")
            new_title = c.get("new_title", "")
            aid = c.get("aid")

            block_lines = [
                f"{idx}️⃣ {old_title}➜➜➜：",
                f"{new_title}",
            ]
            # 每个分P之间再空一行
            lines.append("\n".join(block_lines))
            lines.append("")

    # 失败的部分
    if errors:
        lines.append(f"❌ 以下 {len(errors)} 个分P 修改失败：")
        lines.append("")  # 空行

        for idx, e in enumerate(errors, start=1):
            old_title = e.get("old_title", "")
            new_title = e.get("new_title", "")
            err_msg = e.get("error", "未知错误")
            aid = e.get("aid")

            block_lines = [
                f"{idx}️⃣ {old_title}➜➜➜：",
                f"{new_title}",
                f" 错误：{err_msg}",
            ]
            if aid:
                block_lines.append(f"    aid：{aid}")

            lines.append("\n".join(block_lines))
            lines.append("")

    # 如果啥都没有（极端情况）
    if not lines:
        lines.append("本次没有需要修改的分P。")

    text = "\n".join(lines).rstrip()
    return PlainTextResponse(text)

TARGET_DIR = r"D:\DanmakuRender\Tasks文件\少年不太冷"
FILE_PATH = os.path.join(TARGET_DIR, "offline.txt")

@app.get("/create_offline", response_class=PlainTextResponse)
def create_offline_file():
    """
    自动创建 offline.txt，只返回纯文本结果 (Plain Text Response)
    """
    try:
        # 检查并创建文件夹 (Check and create directory)
        if not os.path.exists(TARGET_DIR):
            os.makedirs(TARGET_DIR)

        # 创建或覆盖文件 (Create or overwrite file)
        with open(FILE_PATH, "w", encoding="utf-8") as f:
            f.write("")

        return PlainTextResponse("创建成功")

    except Exception as e:
        # 打印具体的错误到后台日志，方便你调试
        print(f"创建文件失败，原因: {e}")
        return PlainTextResponse("创建错误")

@app.get("/seasons", response_class=PlainTextResponse)
def get_seasons():
    """
    account: cookie 文件名，如 b站_不可一世冷狗
    """
    cookie_path = f".login_info/b站_不可一世冷狗.json"

    if not os.path.exists(cookie_path):
        raise HTTPException(status_code=404, detail=f"找不到 cookie 文件：{cookie_path}")

    season_list = get_season_list(cookie_path)

    lines = [f"{s['id']} - {s['title']}" for s in season_list]
    return "\n".join(lines)

def get_season_list(cookie_path: str) -> list[dict]:
    with open(cookie_path, "r", encoding="utf-8") as f:
        login_info = json.load(f)

    cookies = {
        item["name"]: item["value"]
        for item in login_info["cookie_info"]["cookies"]
    }

    url = "https://member.bilibili.com/x2/creative/web/seasons"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://member.bilibili.com/",
    }

    season_list = []
    pn = 1

    while True:
        params = {"pn": pn, "ps": 30, "order": "", "sort": "", "draft": 1, "source": 0}
        resp = requests.get(url, params=params, headers=headers, cookies=cookies)
        resp.raise_for_status()
        data = resp.json()

        seasons = data["data"]["seasons"]
        if not seasons:
            break

        for item in seasons:
            season_list.append({
                "id": item["season"]["id"],
                "title": item["season"]["title"]
            })

        if len(season_list) >= data["data"]["total"]:
            break
        pn += 1

    return season_list

