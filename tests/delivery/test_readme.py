"""The README covers what an operator needs, in order (DELIVERY-DOC-001)."""

from __future__ import annotations

import re
from pathlib import Path

SECTIONS = [
    "What WearStream is",
    "Before you start",
    "Run it",
    "Authorize",
    "Check on it",
    "Backfill",
    "Adopting an existing capture tree",
    "Upgrading",
    "Configuration reference",
]


# @spec DELIVERY-DOC-001
def test_the_readme_sections_are_in_order(root: Path) -> None:
    headings = re.findall(r"^## (.+)$", (root / "README.md").read_text(), re.MULTILINE)
    positions = [next(i for i, h in enumerate(headings) if h.startswith(s)) for s in SECTIONS]
    assert positions == sorted(positions)


# @spec DELIVERY-DOC-001
def test_the_readme_says_what_an_operator_must_do(root: Path) -> None:
    text = (root / "README.md").read_text()
    for needed in (
        "hard links",
        "65532",
        "WEARSTREAM_UMASK",
        "docker compose",
        "docs/deploy/wearstream-task.yml",
        "docker exec -it wearstream wearstream auth whoop",
        "docker exec -it wearstream wearstream auth garmin",
        "--read-only --tmpfs /tmp",
        "wearstream status",
        "wearstream config",
        "WEARSTREAM_WHOOP_BACKFILL_FROM",
        "WEARSTREAM_GARMIN_BACKFILL_FROM",
        "gh attestation verify",
        "cosign verify",
        ".env.example",
        "docs/intent/operator/operator-design.md",
    ):
        assert needed in text, needed
