"""Cleaning up after an interrupted run (CAPTURE-REPAIR)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from capture_helpers import Logs, put, put_capture, tree

from wearstream.capture import repair

DAY, OTHER_DAY = "2026-11-20", "2026-11-21"


def _whoop(root: Path, collection: str, day: str = DAY) -> Path:
    return root / "whoop" / collection / f"dt={day}"


@pytest.fixture
def messy(root: Path) -> Path:
    put(_whoop(root, "sleep"), ".tmp_aaaa")
    put(_whoop(root, "sleep"), ".tmp_bbbb")
    put(_whoop(root, "sleep"), ".DS_Store")
    put(_whoop(root, "cycle"), ".tmp_cccc")
    put(_whoop(root, "sleep", OTHER_DAY), ".tmp_dddd")
    put(root / "garmin" / "sleep" / f"dt={DAY}", ".tmp_eeee")
    put_capture(
        _whoop(root, "sleep"),
        "sleep_1_abcdef",
        {"source": "whoop", "collection": "sleep", "sha256": "x"},
    )
    return root


def _hidden(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob(".*"))


# @spec CAPTURE-REPAIR-001, CAPTURE-REPAIR-003
def test_temporary_files_in_the_given_days_are_removed(messy: Path, logs: Logs) -> None:
    repair(messy, "whoop", {DAY})
    assert _hidden(messy) == [
        "garmin/sleep/dt=2026-11-20/.tmp_eeee",
        "whoop/sleep/dt=2026-11-20/.DS_Store",
        "whoop/sleep/dt=2026-11-21/.tmp_dddd",
    ]
    counts = {(e["collection"], e["count"]) for e in logs.events("capture_tmp_removed")}
    assert counts == {("sleep", 2), ("cycle", 1)}
    assert all(
        (e["source"], e["dt"], e["level"]) == ("whoop", DAY, "info")
        for e in logs.events("capture_tmp_removed")
    )


# @spec CAPTURE-REPAIR-002, CAPTURE-REPAIR-003
def test_lone_files_are_left_and_reported(root: Path, logs: Logs) -> None:
    folder = _whoop(root, "sleep")
    put(folder, "sleep_1_aaaaaa.json")
    put(folder, "sleep_2_bbbbbb.meta.json")
    put_capture(folder, "sleep_3_cccccc", {"source": "whoop", "collection": "sleep", "sha256": "x"})
    before = tree(root)
    repair(root, "whoop", {DAY})
    assert tree(root) == before
    reported = sorted(Path(e["path"]).name for e in logs.events("capture_orphan_found"))
    assert reported == ["sleep_1_aaaaaa.json", "sleep_2_bbbbbb.meta.json"]
    assert all(e["level"] == "warning" for e in logs.events("capture_orphan_found"))


# @spec CAPTURE-REPAIR-001
def test_missing_folders_are_not_an_error(root: Path, logs: Logs) -> None:
    repair(root, "whoop", {DAY})
    put(_whoop(root, "sleep", OTHER_DAY), ".tmp_x")
    repair(root, "whoop", {DAY})
    assert logs.events("capture_repair_failed") == []


# @spec CAPTURE-REPAIR-004
def test_an_error_in_one_folder_is_logged_and_repair_continues(
    messy: Path, logs: Logs, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_scandir = os.scandir
    broken = str(_whoop(messy, "sleep"))

    def scandir(path: object = ".") -> object:
        if os.fspath(path) == broken:
            raise PermissionError(13, "Permission denied")
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", scandir)
    repair(messy, "whoop", {DAY})
    [failed] = logs.events("capture_repair_failed")
    assert failed["level"] == "warning" and "PermissionError" in failed["error"]
    assert not (_whoop(messy, "cycle") / ".tmp_cccc").exists()
