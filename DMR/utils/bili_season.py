"""B站「合集(season)」管理 API —— 标题同步。

注意：本模块**不被 DMR 主录制/上传管线使用**，仅供独立工具（api.py 的 FastAPI 接口）调用。
它走 .login_info/<account>.json 里的 cookie 自助鉴权（与 biliwebapi 引擎的登录态相互独立）。
把稿件「加入合集 + 排序」的逻辑已迁到 DMR/Uploader/biliwebapi.py（用引擎自身登录态），勿混用。
"""
import json
import time
import logging
import requests
from typing import List, Dict

logger = logging.getLogger(__name__)


def get_cookies(account):
    login_json = f"D:/DanmakuRender/.login_info/{account}.json"
    with open(login_json, 'r', encoding='utf-8') as f:
        data = json.load(f)
    cookies = {}
    for c in data['cookie_info']['cookies']:
        name = c.get('name')
        value = c.get('value')
        if name and value:
            cookies[name] = value
    return cookies


def build_headers():
    return {
        "Content-Type": "application/json; charset=UTF-8",
        "Origin": "https://member.bilibili.com",
        "Referer": "https://member.bilibili.com/platform/series/manager",
        "User-Agent": "Mozilla/5.0"
    }


def get_section_id_from_season(account, season_id: int) -> int:
    """从 seasonId 获取第一个 sectionId"""
    cookies = get_cookies(account)
    headers = build_headers()
    url = f'https://member.bilibili.com/x2/creative/web/season?id={season_id}'
    r = requests.get(url, headers=headers, cookies=cookies, timeout=5)
    j = r.json()
    if j.get('code') != 0:
        raise RuntimeError(f'获取season信息失败: {j}')
    return j['data']['sections']['sections'][0]['id']


def sync_section_episode_titles(
    account: str,
    season_id: int,
) -> dict:
    """
    返回一个字典，里面包含：
    - changed: 修改成功的分P列表
    - errors: 修改失败的分P列表及错误
    """
    section_id = get_section_id_from_season(account=account, season_id=season_id)

    def _fetch_section_data(sec_id: int) -> dict:
        time.sleep(3)
        r = requests.get(
            url_section,
            headers=headers,
            cookies=cookies,
            params={"id": sec_id},
        )
        r.raise_for_status()
        j = r.json()
        data = j.get("data") or {}
        return data

    def _build_sorts(episodes: list) -> list:
        sorts = []
        for idx, ep in enumerate(episodes, start=1):
            sorts.append({
                "id": ep.get("id"),
                "sort": idx,
            })
        return sorts

    def _set_list_title_same_as_video_title(episode: dict, episodes: list):
        url_episode_edit = "https://member.bilibili.com/x2/creative/web/season/section/episode/edit"

        payload = {
            "aid": episode.get("aid"),
            "cid": episode.get("cid"),
            "id": episode.get("id"),
            "order": episode.get("order"),
            "seasonId": episode.get("seasonId"),
            "sectionId": episode.get("sectionId"),
            "title": episode.get("archiveTitle"),  # 改成视频标题
            "sorts": _build_sorts(episodes),
        }

        time.sleep(3)
        r = requests.post(
            url_episode_edit,
            headers=headers,
            cookies=cookies,
            params={"csrf": cookies["bili_jct"]},
            data=json.dumps(payload).encode("utf-8"),
        )
        r.raise_for_status()

        res = r.json()
        if res.get("code") != 0:
            raise RuntimeError(
                f"B站返回错误: code={res.get('code')}, message={res.get('message')}"
            )

        return True   # 明确返回成功

    # ---------- 主流程 ----------
    url_section = "https://member.bilibili.com/x2/creative/web/season/section"
    cookies = get_cookies(account)
    headers = build_headers()
    data = _fetch_section_data(section_id)
    episodes = data.get("episodes") or []

    if not episodes:
        logger.info(f"section_id={section_id} 下没有分P episodes")
        return {
            "section_id": section_id,
            "changed": [],
            "errors": [],
            "message": "没有分P",
        }

    changed_list: List[Dict] = []
    error_list: List[Dict] = []

    for idx, ep in enumerate(episodes):
        archive_title = ep.get("archiveTitle")
        list_title = ep.get("title")

        if archive_title == list_title:
            continue

        logger.info(f"第{idx}个不一致, 列表标题为:{list_title}, 将改为:{archive_title}")

        try:
            _set_list_title_same_as_video_title(ep, episodes)
            logger.info(f"第{idx}个修改成功")

            changed_list.append({
                "index": idx,
                "episode_id": ep.get("id"),
                "aid": ep.get("aid"),
                "old_title": list_title,
                "new_title": archive_title,
            })

        except Exception as e:
            logger.exception("修改第%d个分P失败: %s", idx, e)
            error_list.append({
                "index": idx,
                "episode_id": ep.get("id"),
                "aid": ep.get("aid"),
                "old_title": list_title,
                "new_title": archive_title,
                "error": str(e),
            })

    if not changed_list and not error_list:
        logger.info("所有分P的列表标题都已经和视频标题一致")
        return {
            "section_id": section_id,
            "changed": [],
            "errors": [],
            "message": "所有分P标题本来就一致",
        }

    return {
        "section_id": section_id,
        "changed": changed_list,
        "errors": error_list,
    }
