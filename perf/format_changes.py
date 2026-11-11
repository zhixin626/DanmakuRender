"""Format only branch additions/changed ranges, with a semantic AST check."""

from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True, encoding="utf8")


def syntax(source):
    tree = ast.parse(source, type_comments=True)
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            if (
                node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
            ):
                if isinstance(node.body[0].value.value, str):
                    node.body[0].value.value = (
                        ""  # Black normalizes docstring whitespace.
                    )
    return ast.dump(tree, include_attributes=False)


def main():
    base = git("merge-base", "HEAD", "origin/zhinxinchange_v5").strip()
    paths = {
        line.split("\t", 1)[1]: line[0]
        for line in git(
            "diff", "--name-status", "--diff-filter=AM", base, "--", "*.py"
        ).splitlines()
    }
    paths.update(
        {
            path: "A"
            for path in git(
                "ls-files", "--others", "--exclude-standard", "--", "*.py"
            ).splitlines()
        }
    )
    results = []
    for relative, status in sorted(paths.items()):
        path = ROOT / relative
        if not path.is_file():
            continue
        before = path.read_text(encoding="utf-8-sig")
        command = [sys.executable, "-m", "black", "--quiet", "--safe"]
        ranges = []
        if status != "A":
            diff = git("diff", "--unified=0", base, "--", relative)
            for start, count in re.findall(
                r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", diff, re.M
            ):
                count = int(count) if count else 1
                if count:
                    ranges.append((int(start), int(start) + count - 1))
            if not ranges:
                continue
            for start, end in ranges:
                command.extend(["--line-ranges", f"{start}-{end}"])
        completed = subprocess.run(
            [*command, str(path)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf8",
        )
        if completed.returncode:
            if status == "A":
                raise RuntimeError(f"{relative}: {completed.stderr}")
            # Black's partial formatter can fail on old one-line suites.
            # The alternative stays within the same approved line intervals.
            path.write_text(before, encoding="utf8")
            for start, end in sorted(ranges, reverse=True):
                subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "autopep8",
                        "--in-place",
                        "--line-range",
                        str(start),
                        str(end),
                        str(path),
                    ],
                    cwd=ROOT,
                    check=True,
                )
        after = path.read_text(encoding="utf-8-sig")
        if syntax(before) != syntax(after):
            path.write_text(before, encoding="utf8")
            raise RuntimeError(f"AST changed, restored file: {relative}")
        results.append(
            {
                "path": relative,
                "new_file": status == "A",
                "ranges": ranges,
                "changed": before != after,
                "ast_equal": True,
                "before_sha256": hashlib.sha256(before.encode()).hexdigest(),
                "after_sha256": hashlib.sha256(after.encode()).hexdigest(),
            }
        )
    output = ROOT / "perf/tmp/night/format.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps({"base": base, "files": results}, ensure_ascii=False, indent=2),
        encoding="utf8",
    )
    print(
        json.dumps(
            {
                "base": base,
                "checked": len(results),
                "changed": sum(r["changed"] for r in results),
                "ast_equal": True,
            }
        )
    )


if __name__ == "__main__":
    main()
