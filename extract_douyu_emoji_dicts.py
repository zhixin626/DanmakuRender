import ast
import json
import re

from playwright.sync_api import sync_playwright
from tree_sitter import Language, Parser
import tree_sitter_javascript


ROOM_URL = "https://www.douyu.com/3125893"
JS_PATTERN = re.compile(r"/js/live-next-player-aside_[^/?]+\.js(?:\?|$)")
PLAIN_KEY = re.compile(r"(?:[A-Za-z_$][A-Za-z0-9_$]*|\d+)$")


def walk(node):
    stack = [node]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed(node.named_children))


def text(source, node):
    return source[node.start_byte:node.end_byte].decode("utf-8", "replace")


def literal(source, node):
    if node is None:
        return None
    value = text(source, node)
    if node.type in ("identifier", "property_identifier", "number"):
        return value
    if node.type == "string":
        try:
            return ast.literal_eval(value)
        except (SyntaxError, ValueError):
            return value[1:-1]


def string_object(source, node):
    if node is None or node.type != "object":
        return None
    result = {}
    for pair in node.named_children:
        if pair.type != "pair":
            return None
        key = literal(source, pair.child_by_field_name("key"))
        value = literal(source, pair.child_by_field_name("value"))
        if key is None or value is None:
            return None
        result[str(key)] = str(value)
    return result or None


def scope(node):
    node = node.parent
    while node and node.type not in (
        "function_expression", "function_declaration", "arrow_function", "program"
    ):
        node = node.parent
    return (node.start_byte, node.end_byte) if node else None


def object_keys_map(source, node):
    if node.type != "call_expression":
        return None
    map_member = node.child_by_field_name("function")
    if map_member is None or map_member.type != "member_expression":
        return None
    if text(source, map_member.child_by_field_name("property")) != "map":
        return None
    keys_call = map_member.child_by_field_name("object")
    if keys_call is None or keys_call.type != "call_expression":
        return None
    keys_member = keys_call.child_by_field_name("function")
    if keys_member is None or text(source, keys_member) != "Object.keys":
        return None
    arguments = keys_call.child_by_field_name("arguments")
    if arguments and len(arguments.named_children) == 1:
        argument = arguments.named_children[0]
        return text(source, argument) if argument.type == "identifier" else None


def extract_dictionaries(javascript):
    source = javascript.encode("utf-8")
    root = Parser(Language(tree_sitter_javascript.language())).parse(source).root_node
    declarations = {}
    for node in walk(root):
        if node.type != "variable_declarator":
            continue
        name = literal(source, node.child_by_field_name("name"))
        mapping = string_object(source, node.child_by_field_name("value"))
        if name and mapping and not all(key.startswith("./") for key in mapping):
            declarations.setdefault((scope(node), name), []).append((node.start_byte, mapping))

    result = []
    for node in walk(root):
        name = object_keys_map(source, node)
        candidates = declarations.get((scope(node), name), []) if name else []
        candidates = [item for item in candidates if item[0] < node.start_byte]
        if candidates:
            result.append((name, max(candidates, key=lambda item: item[0])[1]))
    return result


def format_dictionary(name, mapping):
    lines = [f"{name} = {{"]
    items = list(mapping.items())
    for index, (key, value) in enumerate(items):
        key = key if PLAIN_KEY.fullmatch(key) else json.dumps(key, ensure_ascii=False)
        comma = "," if index + 1 < len(items) else ""
        lines.append(f"    {key}: {json.dumps(value, ensure_ascii=False)}{comma}")
    return "\n".join(lines + ["}"])


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    with page.expect_response(lambda response: JS_PATTERN.search(response.url), timeout=30000) as info:
        page.goto(ROOM_URL, wait_until="commit", timeout=30000)
    javascript = info.value.body().decode("utf-8", "replace")
    browser.close()

dictionaries = extract_dictionaries(javascript)
if not dictionaries:
    raise RuntimeError("当前 JS 中没有解析出表情字典")
print("\n\n".join(format_dictionary(name, mapping) for name, mapping in dictionaries))
