#!/usr/bin/env python3
"""Print the major.minor Python version that `requires-python` in ./pyproject.toml declares.

`requires-python` must be a floor of the form `>=X.Y`; it names the Python the runtime image ships.
Exit status: 0 with the version printed, 1 if the declaration has another form.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path


# @spec DELIVERY-CI-011
def main() -> int:
    declared = tomllib.loads(Path("pyproject.toml").read_text())["project"]["requires-python"]
    match = re.fullmatch(r">=(\d+)\.(\d+)", declared.strip())
    if not match:
        print(f"requires-python must be '>=X.Y', found {declared!r}", file=sys.stderr)
        return 1
    print(f"{match.group(1)}.{match.group(2)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
