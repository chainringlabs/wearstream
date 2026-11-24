"""Freshness: is the newest capture recent enough (CHECKS-FRESH-001..003, CHECKS-SUPP-002)."""

from __future__ import annotations

from pathlib import Path

from checks_helpers import NOW, hours_ago, put, setting

from wearstream.checks import freshness

DAY = NOW.date().isoformat()


# @spec CHECKS-FRESH-001
def test_a_capture_within_the_tolerance_passes(data: Path) -> None:
    put(data, "sleep", DAY, {"a": 1}, at=hours_ago(31.9))
    result = freshness(data, setting(), NOW)
    assert result.severity == "pass"


# @spec CHECKS-FRESH-001
def test_a_newest_capture_older_than_cadence_plus_grace_fails_with_an_action(data: Path) -> None:
    put(data, "sleep", "2026-11-22", {"a": 1}, at=hours_ago(40))
    put(data, "sleep", "2026-11-23", {"a": 1}, at=hours_ago(32.5))
    result = freshness(data, setting(), NOW)
    assert (result.severity, result.status) == ("error", "stale")
    assert "run_failed" in result.action and "run_killed" in result.action
    assert "deselect" in result.action


# @spec CHECKS-FRESH-001
def test_the_tolerance_comes_from_the_collections_settings(data: Path) -> None:
    put(data, "sleep", DAY, {"a": 1}, at=hours_ago(3))
    assert freshness(data, setting(cadence_hours=1, grace_hours=1), NOW).severity == "error"
    assert freshness(data, setting(cadence_hours=2, grace_hours=2), NOW).severity == "pass"


# @spec CHECKS-FRESH-001
def test_only_the_trailing_folders_are_read(data: Path) -> None:
    """A capture filed under an old day but fetched recently is outside a one-folder window."""
    put(data, "sleep", "2026-11-01", {"a": 1}, at=hours_ago(1))
    put(data, "sleep", "2026-11-20", {"a": 1}, at=hours_ago(90))
    assert freshness(data, setting(recent_folders=1), NOW).severity == "error"
    assert freshness(data, setting(recent_folders=2), NOW).severity == "pass"


# @spec CHECKS-FRESH-001
def test_a_stray_file_is_not_a_capture(data: Path) -> None:
    put(data, "sleep", DAY, {"a": 1}, at=hours_ago(50))
    folder = data / "whoop" / "sleep" / f"dt={DAY}"
    (folder / f"sleep_{int(NOW.timestamp() * 1000)}_abcdef.json").write_text("{}")
    assert freshness(data, setting(), NOW).severity == "error"


# @spec CHECKS-FRESH-002
def test_a_collection_never_captured_fails_unless_expected_empty(data: Path) -> None:
    failed = freshness(data, setting(), NOW)
    assert (failed.severity, failed.status) == ("error", "never_captured")
    assert "run_failed" in failed.action
    assert freshness(data, setting(expected_empty=True), NOW).severity == "pass"


# @spec CHECKS-FRESH-003
def test_an_expected_empty_collection_gone_stale_only_warns(data: Path) -> None:
    put(data, "sleep", "2026-11-20", {"a": 1}, at=hours_ago(80))
    result = freshness(data, setting(expected_empty=True), NOW)
    assert (result.severity, result.status) == ("warning", "stale")
    assert "deselect" in result.action


# @spec CHECKS-SUPP-001
def test_a_stale_collection_is_suppressed_while_capture_cannot_happen(data: Path) -> None:
    put(data, "sleep", "2026-11-23", {"a": 1}, at=hours_ago(40))
    result = freshness(data, setting(), NOW, suppressed="waiting_for_auth")
    assert result.suppressed == "waiting_for_auth"
    never = freshness(data, setting("hr"), NOW, suppressed="rate_limited")
    assert never.suppressed == "rate_limited"


# @spec CHECKS-SUPP-001
def test_a_fresh_collection_is_not_marked_suppressed(data: Path) -> None:
    put(data, "sleep", DAY, {"a": 1}, at=hours_ago(2))
    result = freshness(data, setting(), NOW, suppressed="rate_limited")
    assert (result.severity, result.suppressed) == ("pass", None)


# @spec CHECKS-SUPP-002
def test_suppression_ends_beyond_three_tolerances(data: Path) -> None:
    put(data, "sleep", "2026-11-20", {"a": 1}, at=hours_ago(95))
    assert freshness(data, setting(), NOW, suppressed="rate_limited").suppressed == "rate_limited"
    put(data, "hr", "2026-11-20", {"a": 1}, at=hours_ago(97))
    result = freshness(data, setting("hr"), NOW, suppressed="rate_limited")
    assert (result.severity, result.suppressed) == ("error", None)
