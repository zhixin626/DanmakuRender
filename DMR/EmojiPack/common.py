import os
import re
import urllib.request

from DMR.Login.cookiemgr import LOGIN_INFO_DIR

UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://live.bilibili.com/"}
IMAGE_EXTENSIONS = (".png", ".webp", ".gif", ".jpg", ".jpeg")


def safe_name(s):
    """emoji 名 -> 安全文件名：去方括号、去掉非法字符"""
    s = (s or "").strip().strip("[]").strip()
    return re.sub(r'[\\/:*?"<>|\r\n\t]', "_", s)


def latest_cookie_file(prefix):
    """.login_info/<prefix>*.json 里最新的一份，找不到返回 None"""
    cands = sorted(LOGIN_INFO_DIR.glob(f'{prefix}*.json'), key=lambda p: p.stat().st_mtime, reverse=True)
    return cands[0] if cands else None


def existing_names(pack_dir):
    """本地已有的表情文件名（不含扩展名，小写），用于跟接口结果比对缺失"""
    if not os.path.isdir(pack_dir):
        return set()
    return {
        os.path.splitext(fn)[0].lower()
        for fn in os.listdir(pack_dir)
        if fn.lower().endswith(IMAGE_EXTENSIONS)
    }


def count_pack(pack_dir):
    """本地表情包目录里的图片数量，供页面直接展示"""
    return len(existing_names(pack_dir))


def diff_pack(name_to_url: dict, pack_dir: str):
    """返回远端缺失项、本地多余项，以及本地和远端的表情数量。"""
    files = [
        name for name in os.listdir(pack_dir)
        if name.lower().endswith(IMAGE_EXTENSIONS)
    ] if os.path.isdir(pack_dir) else []
    existing = {os.path.splitext(name)[0].lower() for name in files}
    seen = {}
    missing = []
    for name, url in name_to_url.items():
        fname = safe_name(name)
        if not fname:
            continue
        key = fname.lower()
        if key in seen:          # 大小写重复，指向同图，跳过
            continue
        seen[key] = fname
        if key not in existing:
            missing.append({'name': fname, 'url': url})
    extra = [
        {'name': os.path.splitext(filename)[0], 'filename': filename}
        for filename in files
        if os.path.splitext(filename)[0].lower() not in seen
    ]
    return missing, extra, len(existing), len(seen)


def delete_extras(name_to_url: dict, pack_dir: str, items):
    """删除仍不在最近远端清单中的指定图片文件。"""
    _, extra, _, _ = diff_pack(name_to_url, pack_dir)
    allowed = {item['filename'] for item in extra}
    requested = {item.get('filename') for item in items if isinstance(item, dict)}
    deleted, failed = [], []
    root = os.path.abspath(pack_dir)
    for filename in sorted(allowed & requested):
        path = os.path.abspath(os.path.join(root, filename))
        if os.path.dirname(path) != root:
            continue
        try:
            os.remove(path)
            deleted.append(filename)
        except Exception as exc:
            failed.append({'filename': filename, 'error': str(exc)})
    return {'deleted': deleted, 'failed': failed}


def download_one(name, url, pack_dir, headers=None):
    ext = ".png"
    m = re.search(r"\.(png|webp|gif|jpe?g)(?:\?|$)", url, re.I)
    if m:
        ext = "." + m.group(1).lower()
    os.makedirs(pack_dir, exist_ok=True)
    fp = os.path.join(pack_dir, name + ext)
    req = urllib.request.Request(url, headers=headers or UA)
    data = urllib.request.urlopen(req, timeout=20).read()
    if not data:
        raise ValueError("空内容")
    with open(fp, "wb") as f:
        f.write(data)
    return fp


def download_missing(items, pack_dir, headers=None):
    """items: [{name, url}, ...]，逐个下载，返回 {downloaded, failed:[{name,error}]}"""
    downloaded, failed = 0, []
    for it in items:
        try:
            download_one(it['name'], it['url'], pack_dir, headers=headers)
            downloaded += 1
        except Exception as e:
            failed.append({'name': it.get('name'), 'error': str(e)})
    return {'downloaded': downloaded, 'failed': failed}
