"""What counts as a capture, what is ignored, order, and "has data" (CAPTURE-READ)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from capture_helpers import DAY, put, put_capture

from wearstream.capture import capture, has_data, list_captures


def _meta(source: str = "whoop", collection: str = "sleep", **extra: Any) -> dict[str, Any]:
    return {"source": source, "collection": collection, "sha256": "ab" * 32, **extra}


@pytest.fixture
def folder(root: Path) -> Path:
    return root / "whoop" / "sleep" / f"dt={DAY}"


def _stems(root: Path, collection: str = "sleep", day: str = DAY) -> list[str]:
    return [c.stem for c in list_captures(root, "whoop", collection, day)]


# @spec CAPTURE-READ-001
def test_a_capture_is_one_payload_and_one_valid_sidecar(root: Path, folder: Path) -> None:
    put_capture(folder, "sleep_100_abcdef", _meta())
    [found] = list_captures(root, "whoop", "sleep", DAY)
    assert (found.stem, found.ms) == ("sleep_100_abcdef", 100)
    assert found.payload == folder / "sleep_100_abcdef.json"
    assert found.sidecar == folder / "sleep_100_abcdef.meta.json"
    assert found.meta["sha256"] == "ab" * 32


# @spec CAPTURE-READ-001
def test_names_are_parsed_from_the_right(root: Path) -> None:
    folder = root / "whoop" / "daily_summary" / f"dt={DAY}"
    put_capture(folder, "daily_summary_100_abcdef", _meta(collection="daily_summary"))
    assert _stems(root, "daily_summary") == ["daily_summary_100_abcdef"]


# @spec CAPTURE-READ-001
@pytest.mark.parametrize(
    "files",
    [
        {"sleep_1_abcdef.json": "{}"},
        {"sleep_1_abcdef.meta.json": '{"source":"whoop","collection":"sleep","sha256":"x"}'},
        {
            "sleep_1_abcdef.json": "{}",
            "sleep_1_abcdef.csv": "a",
            "sleep_1_abcdef.meta.json": '{"source":"whoop","collection":"sleep","sha256":"x"}',
        },
        {"sleep_1_abcdef.json": "{}", "sleep_1_abcdef.meta.json": "not json"},
        {"sleep_1_abcdef.json": "{}", "sleep_1_abcdef.meta.json": "[1]"},
        {
            "sleep_1_abcdef.json": "{}",
            "sleep_1_abcdef.meta.json": '{"source":"whoop","collection":"sleep","sha256":1}',
        },
        {
            "sleep_1_abcdef.json": "{}",
            "sleep_1_abcdef.meta.json": '{"source":"garmin","collection":"sleep","sha256":"x"}',
        },
        {
            "sleep_1_abcdef.json": "{}",
            "sleep_1_abcdef.meta.json": '{"source":"whoop","collection":"naps","sha256":"x"}',
        },
        {
            "naps_1_abcdef.json": "{}",
            "naps_1_abcdef.meta.json": '{"source":"whoop","collection":"sleep","sha256":"x"}',
        },
        {
            "sleep_1_ABCDEF.json": "{}",
            "sleep_1_ABCDEF.meta.json": '{"source":"whoop","collection":"sleep","sha256":"x"}',
        },
        {
            "sleep_1_abcde.json": "{}",
            "sleep_1_abcde.meta.json": '{"source":"whoop","collection":"sleep","sha256":"x"}',
        },
        {
            "sleep_x1_abcdef.json": "{}",
            "sleep_x1_abcdef.meta.json": '{"source":"whoop","collection":"sleep","sha256":"x"}',
        },
    ],
    ids=[
        "lone-payload",
        "lone-sidecar",
        "two-payloads",
        "sidecar-not-json",
        "sidecar-not-object",
        "sha256-not-string",
        "other-source",
        "other-collection",
        "name-of-other-collection",
        "uppercase-id",
        "short-id",
        "non-digit-ms",
    ],
)
def test_anything_else_is_not_a_capture(root: Path, folder: Path, files: dict[str, str]) -> None:
    for name, data in files.items():
        put(folder, name, data)
    assert list_captures(root, "whoop", "sleep", DAY) == []
    assert not has_data(root, "whoop", "sleep", DAY)


# @spec CAPTURE-READ-002
def test_dotfiles_strangers_and_subfolders_are_ignored(root: Path, folder: Path) -> None:
    put_capture(folder, "sleep_100_abcdef", _meta())
    put(folder, ".tmp_0123abcd", "{}")
    put(folder, ".DS_Store", "x")
    put(folder, "notes.txt", "x")
    put_capture(folder / "sleep_200_abcdef", "sleep_200_abcdef", _meta())
    assert _stems(root) == ["sleep_100_abcdef"]


# @spec CAPTURE-READ-002
def test_a_partition_folder_with_an_impossible_date_is_ignored(root: Path) -> None:
    put_capture(root / "whoop" / "sleep" / "dt=2026-02-30", "sleep_100_abcdef", _meta())
    assert list_captures(root, "whoop", "sleep", "2026-02-30") == []
    assert not has_data(root, "whoop", "sleep", "2026-02-30")


# @spec CAPTURE-READ-003
def test_captures_are_listed_newest_first_with_ties_broken_by_stem(
    root: Path, folder: Path
) -> None:
    for stem in (
        "sleep_100_aaaaaa",
        "sleep_300_aaaaaa",
        "sleep_200_bbbbbb",
        "sleep_200_cccccc",
        "sleep_99_ffffff",
    ):
        put_capture(folder, stem, _meta())
    assert _stems(root) == [
        "sleep_300_aaaaaa",
        "sleep_200_cccccc",
        "sleep_200_bbbbbb",
        "sleep_100_aaaaaa",
        "sleep_99_ffffff",
    ]


# @spec CAPTURE-READ-004
def test_a_folder_has_data_only_when_it_holds_a_capture(root: Path, folder: Path) -> None:
    assert not has_data(root, "whoop", "sleep", DAY)
    put(folder, "sleep_1_abcdef.json", "{}")
    assert not has_data(root, "whoop", "sleep", DAY)
    capture(root, "whoop", "sleep", b"{}", {"http_status": 200}, dt=DAY)
    assert has_data(root, "whoop", "sleep", DAY)
    assert not has_data(root, "whoop", "sleep", "2026-11-21")
