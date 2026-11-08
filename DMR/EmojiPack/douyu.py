"""从斗鱼当前 Webpack bundle 中提取并下载弹幕表情。

不依赖 bundle 文件名、模块 ID、变量名或已知表情名称。解析器通过 AST 识别：
名称对象 -> Object.keys(...).map(...) -> PNG context -> 图片模块 URL。
"""

import argparse
import ast
import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path

from . import common

PACK_DIR = os.path.join("emoji_pack", "douyu")
MANIFEST = os.path.join(PACK_DIR, "douyu_emoji.json")
ROOM_ID = "3125893"
ROOM_URL = "https://www.douyu.com/{room_id}"
MAIN_BUNDLE_PATTERN = re.compile(r"/js/live-next-player-aside_[^/?]+\.js(?:\?|$)")
IMAGE_EXTENSIONS = (".png", ".webp", ".gif", ".jpg", ".jpeg")


def _javascript_parser():
    try:
        from tree_sitter import Language, Parser
        import tree_sitter_javascript
    except ImportError as exc:
        raise RuntimeError(
            "解析斗鱼表情需要 tree-sitter 和 tree-sitter-javascript，请重新安装 requirements.txt"
        ) from exc
    return Parser(Language(tree_sitter_javascript.language()))


def _walk(node):
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(reversed(current.named_children))


def _text(source, node):
    return source[node.start_byte:node.end_byte].decode("utf-8", "replace")


def _literal(source, node):
    text = _text(source, node)
    if node.type in ("identifier", "property_identifier", "number"):
        return text
    if node.type != "string":
        return None
    try:
        return ast.literal_eval(text)
    except (SyntaxError, ValueError):
        return text[1:-1]


def _string_object(source, node):
    """读取值全部为字符串的对象；不是这种对象则返回 None。"""
    if node is None or node.type != "object":
        return None
    result = {}
    for child in node.named_children:
        if child.type != "pair":
            return None
        key = _literal(source, child.child_by_field_name("key"))
        value = _literal(source, child.child_by_field_name("value"))
        if key is None or value is None:
            return None
        result[str(key)] = str(value)
    return result or None


def _module_functions(source, root):
    """返回 Webpack 模块表中的 module_id -> function node。"""
    modules = {}
    for node in _walk(root):
        if node.type != "pair":
            continue
        value = node.child_by_field_name("value")
        if value is None or value.type not in ("function_expression", "arrow_function"):
            continue
        module_id = _literal(source, node.child_by_field_name("key"))
        if module_id:
            modules[str(module_id)] = value
    return modules


def _module_data(source, modules):
    """提取 PNG context 映射和直接导出的图片 URL。"""
    contexts, image_urls = {}, {}
    for module_id, function in modules.items():
        best_context = None
        for node in _walk(function):
            if node.type == "object":
                candidate = _string_object(source, node)
                if candidate and all(
                    key.startswith("./") and key.lower().endswith(IMAGE_EXTENSIONS)
                    for key in candidate
                ):
                    if best_context is None or len(candidate) > len(best_context):
                        best_context = candidate
            elif node.type == "assignment_expression":
                left = node.child_by_field_name("left")
                right = node.child_by_field_name("right")
                url = _literal(source, right) if right is not None else None
                if left is not None and url and _text(source, left).endswith(".exports"):
                    if urllib.parse.urlparse(url).path.lower().endswith(IMAGE_EXTENSIONS):
                        image_urls[module_id] = url
        if best_context:
            contexts[module_id] = best_context
    return contexts, image_urls


def _identifier(node, source):
    return _text(source, node) if node is not None and node.type == "identifier" else None


def _object_keys_map(source, node):
    """若 node 是 Object.keys(mapping).map(callback)，返回 mapping 名与 callback。"""
    if node.type != "call_expression":
        return None
    map_member = node.child_by_field_name("function")
    if map_member is None or map_member.type != "member_expression":
        return None
    if _text(source, map_member.child_by_field_name("property")) != "map":
        return None

    keys_call = map_member.child_by_field_name("object")
    if keys_call is None or keys_call.type != "call_expression":
        return None
    keys_member = keys_call.child_by_field_name("function")
    if keys_member is None or keys_member.type != "member_expression":
        return None
    if (
        _text(source, keys_member.child_by_field_name("object")) != "Object"
        or _text(source, keys_member.child_by_field_name("property")) != "keys"
    ):
        return None

    keys_args = keys_call.child_by_field_name("arguments")
    map_args = node.child_by_field_name("arguments")
    if keys_args is None or map_args is None:
        return None
    mapping_nodes = keys_args.named_children
    callbacks = [
        child for child in map_args.named_children
        if child.type in ("function_expression", "arrow_function")
    ]
    if len(mapping_nodes) != 1 or len(callbacks) != 1:
        return None
    mapping_name = _identifier(mapping_nodes[0], source)
    return (mapping_name, callbacks[0]) if mapping_name else None


def _callback_contexts(source, callback, mapping_name):
    """验证 map 回调确实把 mapping[key] 与同名 PNG 组合为表情对象。"""
    parameters = callback.child_by_field_name("parameters")
    parameter_names = [
        _identifier(node, source) for node in (parameters.named_children if parameters else [])
    ]
    parameter_names = [name for name in parameter_names if name]
    if not parameter_names:
        return []
    key_name = parameter_names[0]

    has_title_lookup = False
    context_ids = []
    for node in _walk(callback):
        if node.type == "subscript_expression":
            obj = node.child_by_field_name("object")
            index = node.child_by_field_name("index")
            if _identifier(obj, source) == mapping_name and _identifier(index, source) == key_name:
                has_title_lookup = True
        elif node.type == "call_expression":
            inner_call = node.child_by_field_name("function")
            if inner_call is None or inner_call.type != "call_expression":
                continue
            outer_args = node.child_by_field_name("arguments")
            inner_args = inner_call.child_by_field_name("arguments")
            if outer_args is None or inner_args is None or len(inner_args.named_children) != 1:
                continue
            module_id = _literal(source, inner_args.named_children[0])
            path_expression = _text(source, outer_args)
            if module_id and key_name in path_expression and ".png" in path_expression:
                context_ids.append(str(module_id))

    return list(dict.fromkeys(context_ids)) if has_title_lookup else []


def _scope_key(node):
    current = node.parent
    while current is not None and current.type not in (
        "function_expression", "function_declaration", "arrow_function", "program"
    ):
        current = current.parent
    return (current.start_byte, current.end_byte) if current is not None else None


def _emoji_packs(source, root):
    mappings = {}
    for node in _walk(root):
        if node.type != "variable_declarator":
            continue
        name = _identifier(node.child_by_field_name("name"), source)
        values = _string_object(source, node.child_by_field_name("value"))
        if name and values and not all(key.startswith("./") for key in values):
            mappings.setdefault((_scope_key(node), name), []).append((node.start_byte, values))

    packs = []
    for node in _walk(root):
        matched = _object_keys_map(source, node)
        if not matched:
            continue
        mapping_name, callback = matched
        candidates = [
            item for item in mappings.get((_scope_key(node), mapping_name), [])
            if item[0] < node.start_byte
        ]
        if not candidates:
            continue
        names = max(candidates, key=lambda item: item[0])[1]
        context_ids = _callback_contexts(source, callback, mapping_name)
        if context_ids:
            packs.append({
                "variable": mapping_name,
                "names": names,
                "contexts": context_ids,
            })
    return packs


def extract_sources(sources):
    """解析 [(source_url, javascript)]，返回 (name_to_url, details)。"""
    parser = _javascript_parser()
    packs, contexts, image_urls = [], {}, {}
    for source_url, javascript in sources:
        source = javascript.encode("utf-8")
        root = parser.parse(source).root_node
        modules = _module_functions(source, root)
        source_contexts, source_urls = _module_data(source, modules)
        contexts.update(source_contexts)
        image_urls.update(source_urls)
        for pack in _emoji_packs(source, root):
            pack["source"] = source_url
            packs.append(pack)

    result, details = {}, []
    for pack in packs:
        for key, title in pack["names"].items():
            path = f"./{key}.png"
            urls = []
            for context_id in pack["contexts"]:
                image_module = contexts.get(context_id, {}).get(path)
                url = image_urls.get(image_module)
                if url and url not in urls:
                    urls.append(url)
            name = f"[{title}]"
            details.append({
                "name": name,
                "key": key,
                "urls": urls,
                "contexts": pack["contexts"],
                "source": pack["source"],
            })
            if urls:
                result.setdefault(name, urls[0])
    return result, details


def _read_source(location, room_id=ROOM_ID):
    if urllib.parse.urlparse(location).scheme in ("http", "https"):
        request = urllib.request.Request(
            location,
            headers={**common.UA, "Referer": ROOM_URL.format(room_id=room_id)},
        )
        return urllib.request.urlopen(request, timeout=30).read().decode("utf-8", "replace")
    return Path(location).read_text(encoding="utf-8")


def _discover_sources(room_id=ROOM_ID):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("自动发现斗鱼 JS 需要 playwright，请先安装 requirements.txt") from exc

    with sync_playwright() as playwright:
        browser = None
        launch_errors = []
        for options in ({}, {"channel": "msedge"}, {"channel": "chrome"}):
            try:
                browser = playwright.chromium.launch(headless=True, **options)
                break
            except Exception as exc:
                launch_errors.append(exc)
        if browser is None:
            raise RuntimeError(
                "无法启动 Chromium、Edge 或 Chrome，请执行 playwright install chromium"
            ) from launch_errors[0]
        context = browser.new_context(service_workers="block")
        page = context.new_page()
        with page.expect_response(
            lambda response: MAIN_BUNDLE_PATTERN.search(response.url),
            timeout=30000,
        ) as response_info:
            page.goto(
                ROOM_URL.format(room_id=room_id),
                wait_until="commit",
                timeout=30000,
            )
        response = response_info.value
        sources = [(response.url, response.body().decode("utf-8", "replace"))]
        browser.close()
    if not sources:
        raise RuntimeError("没有发现斗鱼 live-next-player-aside 的 JS 资源")
    return sources


def _load_sources(js_source=None, room_id=ROOM_ID):
    if js_source:
        return [(js_source, _read_source(js_source, room_id))]
    return _discover_sources(room_id)


def check(js_source=None, room_id=ROOM_ID):
    sources = _load_sources(js_source, room_id)
    merged, details = extract_sources(sources)
    if not merged:
        raise RuntimeError("当前斗鱼 JS 中未解析出表情 URL，已保留本地表情库")
    unresolved = [item["name"] for item in details if not item["urls"]]
    if unresolved:
        sample = ", ".join(unresolved[:5])
        raise RuntimeError(
            f"斗鱼表情解析不完整：{len(unresolved)} 个表情缺少图片 URL"
            f"（例如 {sample}），已保留本地表情库"
        )

    os.makedirs(PACK_DIR, exist_ok=True)
    manifests = {
        MANIFEST: merged,
        os.path.join(PACK_DIR, "douyu_emoji_sources.json"): details,
    }
    temporary = []
    try:
        for path, data in manifests.items():
            temp_path = path + ".tmp"
            with open(temp_path, "w", encoding="utf-8", newline="\n") as file:
                json.dump(data, file, ensure_ascii=False, indent=2)
                file.write("\n")
            temporary.append((temp_path, path))
        for temp_path, path in temporary:
            os.replace(temp_path, path)
    finally:
        for temp_path, _ in temporary:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    missing, extra, existing_count, unique_count = common.diff_pack(merged, PACK_DIR)
    return {
        "missing": missing,
        "extra": extra,
        "existing_count": existing_count,
        "unique_count": unique_count,
        "bundles": [source_url for source_url, _ in sources],
    }


def download(items):
    return common.download_missing(
        items,
        PACK_DIR,
        headers={**common.UA, "Referer": ROOM_URL.format(room_id=ROOM_ID)},
    )


def delete_extras(items):
    with open(MANIFEST, encoding="utf-8") as file:
        return common.delete_extras(json.load(file), PACK_DIR, items)


def main():
    parser = argparse.ArgumentParser(description="检查并更新斗鱼弹幕表情包")
    parser.add_argument("--js", help="本地 JS 文件或完整 URL；不填则后台打开斗鱼自动发现")
    parser.add_argument("--room-id", default=ROOM_ID, help="用于自动发现资源的斗鱼房间号")
    parser.add_argument("--download", action="store_true", help="下载检查出的缺失表情")
    args = parser.parse_args()

    result = check(js_source=args.js, room_id=args.room_id)
    if args.download:
        result["download"] = download(result["missing"])
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
