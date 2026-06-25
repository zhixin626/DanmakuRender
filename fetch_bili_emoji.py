# -*- coding: utf-8 -*-
"""
B 站直播表情下载器(按房间分类,配合 emoji_render 的表情包使用)

用法:
  1) 接口模式(用 cookie + 房间号直接抓):
       python fetch_bili_emoji.py <房间号> [--cookie x.json] [--base emoji_pack/bilibili]
     通用表情 -> base/ ; 该房间专属表情 -> base/<房间号>/

  2) 本地响应模式(浏览器存下的 GetEmoticons 响应 json):
       python fetch_bili_emoji.py --json resp.json --room <房间号> [--base emoji_pack/bilibili]

  3) 通用列表模式(名字<制表符>url,抖音/斗鱼等用):
       python fetch_bili_emoji.py --list list.txt --out 目录

说明:
  - 接口返回的"能发的表情"和登录账号有关:有的账号返回内联小表情([dog][花]…),
    有的返回大表情(啊/冲鸭…)。要哪套就用对应账号的 cookie;或用浏览器存的响应走 --json。
  - 表情图按 弹幕里的[名字] 命名(去方括号):[dog]->dog.png。
"""
import os, sys, json, glob, re, urllib.request, urllib.parse

API = "https://api.live.bilibili.com/xlive/web-ucenter/v2/emoticon/GetEmoticons"
UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://live.bilibili.com/"}


def load_cookie(path):
    d = json.load(open(path, encoding="utf-8"))
    cks = d.get("cookie_info", {}).get("cookies")
    if cks:
        return "; ".join(f"{c['name']}={c['value']}" for c in cks)
    if isinstance(d, dict):
        return "; ".join(f"{k}={v}" for k, v in d.items() if isinstance(v, str))
    return ""


def safe_name(s):
    s = (s or "").strip().strip("[]").strip()
    return re.sub(r'[\\/:*?"<>|\r\n\t]', "_", s)


def download_one(name, url, out_dir):
    ext = ".png"
    m = re.search(r"\.(png|webp|gif|jpe?g)(?:\?|$)", url, re.I)
    if m:
        ext = "." + m.group(1).lower()
    fp = os.path.join(out_dir, name + ext)
    if os.path.exists(fp):
        return "skip"
    try:
        os.makedirs(out_dir, exist_ok=True)
        req = urllib.request.Request(url, headers=UA)
        data = urllib.request.urlopen(req, timeout=20).read()
        if not data:
            return "fail"
        open(fp, "wb").write(data)
        return "ok"
    except Exception as e:
        print(f"  失败 {name}: {e}")
        return "fail"


def download_emoticons(emoticons, out_dir):
    ok = skip = fail = 0
    for e in emoticons:
        name = safe_name(e.get("emoji") or e.get("descript") or "")
        url = e.get("url")
        if not (name and url):
            continue
        r = download_one(name, url, out_dir)
        ok += r == "ok"; skip += r == "skip"; fail += r == "fail"
    return ok, skip, fail


def process_response(d, base, room=None):
    """所有表情(通用 + 房间专属/UP主)都下到 base/(不再分房间号)"""
    if d.get("code") != 0:
        print(f"接口返回 code={d.get('code')} msg={d.get('message')}(cookie 可能过期/换个账号)")
        return
    packs = d.get("data", {}).get("data", [])
    total_ok = total_skip = total_fail = 0
    for p in packs:
        ems = p.get("emoticons", [])
        ok, skip, fail = download_emoticons(ems, base)
        total_ok += ok; total_skip += skip; total_fail += fail
        print(f"  [{p.get('pkg_name') or p.get('pkg_type')}] {len(ems)}个 -> {base}  (新{ok}/跳{skip}/失败{fail})")
    print(f"合计: 新下载 {total_ok}, 跳过 {total_skip}, 失败 {total_fail}")


def main():
    argv = sys.argv[1:]
    def opt(n, d=None):
        return argv[argv.index(n) + 1] if n in argv else d
    pos = [a for a in argv if not a.startswith("--") and
           (argv.index(a) == 0 or not argv[argv.index(a) - 1].startswith("--"))]

    # 通用列表模式
    if "--list" in argv:
        out = opt("--out", "emoji_pack")
        os.makedirs(out, exist_ok=True)
        seen = {}
        for line in open(opt("--list"), encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            parts = re.split(r"\t+| (?=https?://)|\s{2,}", line, maxsplit=1)
            if len(parts) < 2:
                parts = line.split(None, 1)
            if len(parts) < 2 or not parts[1].strip().startswith("http"):
                continue
            seen.setdefault(safe_name(parts[0]), parts[1].strip())
        print(f"列表 {len(seen)} 个 -> {out}")
        ok = skip = fail = 0
        for nm, u in seen.items():
            r = download_one(nm, u, out)
            ok += r == "ok"; skip += r == "skip"; fail += r == "fail"
        print(f"完成: 新{ok}/跳{skip}/失败{fail} -> {os.path.abspath(out)}")
        return

    base = opt("--base", os.path.join("emoji_pack", "bilibili"))

    # 本地响应模式
    if "--json" in argv:
        d = json.load(open(opt("--json"), encoding="utf-8"))
        room = opt("--room", "")
        print(f"解析本地响应 -> base={base} room={room or '(无,全部按通用)'}")
        process_response(d, base, room)
        return

    # 接口模式:需要房间号
    room = pos[0] if pos else opt("--room")
    if not room:
        print(__doc__); sys.exit(1)
    cookie_path = opt("--cookie")
    if not cookie_path:
        cands = sorted(glob.glob(".login_info/b站_*.json"), key=os.path.getmtime, reverse=True)
        if not cands:
            print("找不到 B站 cookie(.login_info/b站_*.json),用 --cookie 指定,或用 --json 走浏览器响应")
            sys.exit(1)
        cookie_path = cands[0]
    print(f"房间 {room} | cookie {cookie_path} | 输出 {base}")
    url = API + "?" + urllib.parse.urlencode({"platform": "pc", "room_id": room})
    req = urllib.request.Request(url, headers={**UA, "Cookie": load_cookie(cookie_path)})
    d = json.load(urllib.request.urlopen(req, timeout=20))
    process_response(d, base, room)
    print(f"完成 -> {os.path.abspath(base)}")


if __name__ == "__main__":
    main()
