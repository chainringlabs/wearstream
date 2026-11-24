#!/usr/bin/env python3
"""Check file and function size limits for Python under `src/`, `scripts/` and `tests/`.

A file may have at most 300 physical lines; a function or method may span at most 50 lines, from
its `def` line to its last line by the syntax tree (decorators not counted). Exit status: 0 when
every file is within the limits, 1 otherwise.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

CHECKED = ("src", "scripts", "tests")
MAX_FILE_LINES = 300
MAX_FUNCTION_LINES = 50


def problems_in(path: Path, root: Path) -> list[str]:
    text = path.read_text()
    shown = path.relative_to(root)
    found = []
    lines = len(text.splitlines())
    if lines > MAX_FILE_LINES:
        found.append(f"{shown}: {lines} lines (limit {MAX_FILE_LINES})")
    for node in ast.walk(ast.parse(text, filename=str(path))):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            span = node.end_lineno - node.lineno + 1
            if span > MAX_FUNCTION_LINES:
                found.append(
                    f"{shown}:{node.lineno}: function {node.name} spans {span} lines "
                    f"(limit {MAX_FUNCTION_LINES})"
                )
    return found


# @spec DELIVERY-CI-006
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path.cwd())
    root = parser.parse_args(argv).root.resolve()

    problems = []
    for folder in CHECKED:
        for path in sorted((root / folder).rglob("*.py")):
            problems.extend(problems_in(path, root))

    for problem in problems:
        print(problem)
    if problems:
        return 1
    print("ok: every file and function is within the size limits")
    return 0


if __name__ == "__main__":
    sys.exit(main())
