"""The three configured directories, and the check that they are distinct."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_DIRS = ("WEARSTREAM_DATA_DIR", "WEARSTREAM_STATE_DIR", "WEARSTREAM_SECRETS_DIR")


@dataclass(frozen=True)
class Paths:
    data: Path
    state: Path
    secrets: Path

    def data_path(self, *parts: str) -> Path:
        return self.data.joinpath(*parts)

    def state_path(self, *parts: str) -> Path:
        return self.state.joinpath(*parts)

    def secrets_path(self, *parts: str) -> Path:
        return self.secrets.joinpath(*parts)


# @spec OPERATOR-PATH-001
def paths(values: dict[str, Any]) -> Paths:
    """The configured directories; every path the designs name below them is relative to these."""
    return Paths(*(Path(values[name]) for name in _DIRS))


def _same(a: Path, b: Path) -> bool:
    if a.exists() and b.exists():
        first, second = os.stat(a), os.stat(b)
        return (first.st_dev, first.st_ino) == (second.st_dev, second.st_ino)
    return a.resolve() == b.resolve()


# @spec OPERATOR-PATH-002
def path_problems(values: dict[str, Any]) -> list[str]:
    present = [name for name in _DIRS if name in values]
    found = []
    for i, first in enumerate(present):
        for second in present[i + 1 :]:
            a, b = Path(values[first]), Path(values[second])
            if _same(a, b):
                found.append(f"{first} and {second} are the same folder")
            elif a.resolve().is_relative_to(b.resolve()) or b.resolve().is_relative_to(a.resolve()):
                found.append(f"{first} and {second}: one is inside the other")
    return found
