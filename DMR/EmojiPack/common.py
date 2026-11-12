import hashlib
import io
import json
import logging
import os
import re
import urllib.parse
import urllib.request
from PIL import Image

log = logging.getLogger(__name__)

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


class EmoticonStore:
    """纯 emoticon 的本地表情库：按 unique 记账，图片按 URL 哈希落盘。"""

    MAX_BYTES = 10 * 1024 * 1024
    FORMAT_EXT = {"PNG": ".png", "WEBP": ".webp", "GIF": ".gif", "JPEG": ".jpg"}

    def __init__(self, directory):
        self.directory = os.path.abspath(directory) if directory else None
        self.registry_path = (
            os.path.join(self.directory, "emoticon.json") if self.directory else None
        )
        self.registry = {}
        self.dirty = False
        self._resolved = {}
        if not self.directory:
            return
        os.makedirs(self.directory, exist_ok=True)
        try:
            with open(self.registry_path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self.registry = data
        except FileNotFoundError:
            pass
        except Exception as e:
            log.warning("读取 emoticon.json 失败，将按空表情库处理: %s", e)

    @staticmethod
    def _normalized_url(url):
        url = str(url or "").strip()
        if not url:
            return ""
        p = urllib.parse.urlsplit(url)
        if p.scheme not in ("http", "https"):
            return ""
        # B站同一资源会混用 http/https，统一成 https 避免重复缓存。
        scheme = (
            "https" if (p.hostname or "").lower().endswith("hdslb.com") else p.scheme
        )
        return urllib.parse.urlunsplit((scheme, p.netloc, p.path, p.query, ""))

    @staticmethod
    def _key(meta, url):
        unique = str(meta.get("emoticon_unique") or "").strip()
        return unique or ("url_" + hashlib.sha256(url.encode("utf-8")).hexdigest())

    def _entry_path(self, entry):
        filename = os.path.basename(str(entry.get("file") or ""))
        return os.path.join(self.directory, filename) if filename else None

    def _download(self, url):
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
        if host != "hdslb.com" and not host.endswith(".hdslb.com"):
            raise ValueError(f"拒绝非 B站图片域名: {host or url}")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            declared = int(resp.headers.get("Content-Length") or 0)
            if declared > self.MAX_BYTES:
                raise ValueError(f"图片过大: {declared} bytes")
            data = resp.read(self.MAX_BYTES + 1)
        if len(data) > self.MAX_BYTES:
            raise ValueError(f"图片超过 {self.MAX_BYTES} bytes")

        with Image.open(io.BytesIO(data)) as im:
            image_format = (im.format or "").upper()
            im.verify()
        ext = self.FORMAT_EXT.get(image_format)
        if not ext:
            raise ValueError(f"不支持的图片格式: {image_format or 'unknown'}")

        filename = hashlib.sha256(url.encode("utf-8")).hexdigest() + ext
        target = os.path.join(self.directory, filename)
        temp = target + f".{os.getpid()}.part"
        try:
            with open(temp, "wb") as f:
                f.write(data)
            os.replace(temp, target)
        finally:
            try:
                if os.path.exists(temp):
                    os.remove(temp)
            except OSError:
                pass
        return filename, target

    def resolve(self, meta):
        """返回该条纯 emoticon 的精确本地图片；无法补齐时返回 None。"""
        if not self.directory or not isinstance(meta, dict):
            return None
        url = self._normalized_url(meta.get("url"))
        key = self._key(meta, url)
        memo_key = (key, url)
        if memo_key in self._resolved:
            return self._resolved[memo_key]

        entry = self.registry.get(key)
        if isinstance(entry, dict):
            path = self._entry_path(entry)
            entry_url = self._normalized_url(entry.get("url"))
            if path and os.path.isfile(path) and (not url or entry_url == url):
                self._resolved[memo_key] = path
                return path

        if not url:
            log.warning(
                "emoticon 缺少可用 URL 且本地未命中: %s", meta.get("name") or key
            )
            self._resolved[memo_key] = None
            return None

        try:
            filename, path = self._download(url)
        except Exception as e:
            log.warning("emoticon 下载失败 %s: %s", meta.get("name") or key, e)
            self._resolved[memo_key] = None
            return None

        self.registry[key] = {
            "name": meta.get("name"),
            "url": url,
            "width": meta.get("width"),
            "height": meta.get("height"),
            "is_dynamic": meta.get("is_dynamic", 0),
            "file": filename,
        }
        self.dirty = True
        self._resolved[memo_key] = path
        log.info("已补齐 emoticon: %s", meta.get("name") or key)
        return path

    def save(self):
        if not self.directory or not self.dirty:
            return
        # 写入前重新合并磁盘内容，降低多个渲染任务同时补表情时互相覆盖的概率。
        merged = {}
        try:
            with open(self.registry_path, encoding="utf-8") as f:
                old = json.load(f)
            if isinstance(old, dict):
                merged.update(old)
        except FileNotFoundError:
            log.debug("emoticon 注册表尚未创建: %s", self.registry_path)
        except Exception as error:
            log.warning("合并 emoticon 注册表失败: %s", error)
        merged.update(self.registry)
        temp = self.registry_path + f".{os.getpid()}.tmp"
        try:
            with open(temp, "w", encoding="utf-8") as f:
                json.dump(merged, f, ensure_ascii=False, indent=2)
            os.replace(temp, self.registry_path)
        finally:
            try:
                if os.path.exists(temp):
                    os.remove(temp)
            except OSError as error:
                log.debug("清理 emoticon 临时文件失败: %s", error)
