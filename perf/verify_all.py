"""Fast pre-commit checks for the refactored renderer."""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]


def source_files() -> list[pathlib.Path]:
    return [*ROOT.glob("DMR/**/*.py"), *ROOT.glob("perf/*.py")]


def main() -> int:
    failures: list[str] = []
    test = subprocess.run([sys.executable, "-m", "pytest", "tests", "-q"], cwd=ROOT)
    if test.returncode:
        failures.append("pytest")
    forbidden = re.compile(r"D:\\DanmakuRender(?!-perf)")
    for path in source_files():
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            if forbidden.search(line):
                failures.append(f"external path: {path}")
    cache_refs = []
    for path in source_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "emoticon-dir" in text:
            cache_refs.append((path, text))
    for path, text in cache_refs:
        if "emoji_pack" not in text and path.name != "emoji_render_mt.py":
            failures.append(f"emoticon cache path: {path}")
    if failures:
        print("verify_all failed:", *failures, sep="\n- ")
        return 1
    print("verify_all passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
