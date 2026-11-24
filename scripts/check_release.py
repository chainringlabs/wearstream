#!/usr/bin/env python3
"""Check a release tag before an image is published (run from the repository root).

For any ref other than a `v*` tag this does nothing. For a `v*` tag it requires that the tag is
`v` plus the `pyproject.toml` version, that the version is a plain `X.Y.Z`, and that the checked-out
commit is on `main` (`origin/main` when present, else `main`). Exit status: 0 or 1.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from pathlib import Path


def _git_ok(*args: str) -> bool:
    return subprocess.run(["git", *args], capture_output=True).returncode == 0


def _main_ref() -> str | None:
    for ref in ("origin/main", "main"):
        if _git_ok("rev-parse", "--verify", "--quiet", ref):
            return ref
    return None


def problems(tag: str) -> list[str]:
    version = tomllib.loads(Path("pyproject.toml").read_text())["project"]["version"]
    found = []
    if tag != f"v{version}":
        found.append(f"tag {tag} does not match the pyproject.toml version {version}")
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        found.append(f"version {version} is not a plain X.Y.Z release")
    main_ref = _main_ref()
    if main_ref is None:
        found.append("no main branch to check the tagged commit against")
    elif not _git_ok("merge-base", "--is-ancestor", "HEAD", main_ref):
        found.append(f"the tagged commit is not on main ({main_ref})")
    return found


# @spec DELIVERY-REL-001
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ref", required=True, help="the Git ref that triggered the workflow")
    ref = parser.parse_args(argv).ref
    if not ref.startswith("refs/tags/v"):
        print(f"not a release tag ({ref}); nothing to check")
        return 0
    found = problems(ref.removeprefix("refs/tags/"))
    for problem in found:
        print(problem)
    if found:
        return 1
    print(f"ok: {ref} is a release of the version in pyproject.toml, on main")
    return 0


if __name__ == "__main__":
    sys.exit(main())
