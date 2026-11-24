#!/usr/bin/env python3
"""Check that every spec citation resolves to an existing EARS spec, and no spec ID repeats.

Spec IDs come from `docs/intent/**/*-specs.md` lines of the form `- [ ] **ID**: ...`. Citations
are comment annotations naming one or more IDs, read from `src/`, `tests/`, `scripts/`, the
`Dockerfile` and `.github/`. Exit status: 0 when everything resolves, 1 otherwise.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

SPEC_LINE = re.compile(r"^- \[[ xD]\] \*\*([A-Z0-9-]+)\*\*", re.MULTILINE)
CITATION = re.compile("#\\s*@" + r"spec\s+([A-Z0-9-]+(?:\s*,\s*[A-Z0-9-]+)*)")
SCANNED_DIRS = ("src", "tests", "scripts", ".github")
SCANNED_FILES = ("Dockerfile",)


def spec_ids(root: Path) -> Counter[str]:
    counts: Counter[str] = Counter()
    for path in sorted((root / "docs" / "intent").glob("**/*-specs.md")):
        counts.update(SPEC_LINE.findall(path.read_text()))
    return counts


def scanned_files(root: Path) -> list[Path]:
    files = [root / name for name in SCANNED_FILES if (root / name).is_file()]
    for folder in SCANNED_DIRS:
        files.extend(p for p in sorted((root / folder).rglob("*")) if p.is_file())
    return files


def citations(path: Path) -> list[tuple[int, str]]:
    try:
        text = path.read_text()
    except UnicodeDecodeError, OSError:
        return []
    found = []
    for number, line in enumerate(text.splitlines(), start=1):
        for match in CITATION.finditer(line):
            found.extend((number, ident.strip()) for ident in match.group(1).split(","))
    return found


# @spec DELIVERY-CI-005
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path.cwd())
    root = parser.parse_args(argv).root.resolve()

    known = spec_ids(root)
    problems = [f"spec ID {ident} is defined {n} times" for ident, n in known.items() if n > 1]
    for path in scanned_files(root):
        for number, ident in citations(path):
            if ident not in known:
                problems.append(f"{path.relative_to(root)}:{number}: unknown spec ID {ident}")

    for problem in problems:
        print(problem)
    if problems:
        return 1
    print(f"ok: {len(known)} spec IDs, every citation resolves")
    return 0


if __name__ == "__main__":
    sys.exit(main())
