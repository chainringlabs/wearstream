"""When freshness and `gap_to_now` are suppressed, read from `/state` (CHECKS-SUPP-001)."""

from __future__ import annotations

from pathlib import Path

import pytest
from checks_helpers import NOW, heartbeat, hours_ago, write_state

from wearstream.checks import suppression_reason
from wearstream.state import iso

SOURCE_DOC = "sources/whoop.json"


# @spec CHECKS-SUPP-001
def test_an_active_source_is_not_suppressed(state: Path) -> None:
    heartbeat(state)
    write_state(state, SOURCE_DOC, {"state": "active"})
    assert suppression_reason(state, "whoop", NOW) is None


# @spec CHECKS-SUPP-001
@pytest.mark.parametrize(
    ("doc", "reason"),
    [
        ({"state": "waiting_for_auth"}, "waiting_for_auth"),
        ({"state": "active", "cooldown_until": iso(hours_ago(-1))}, "rate_limited"),
        (
            {"resumed_at": iso(hours_ago(2)), "last_source_success_at": iso(hours_ago(3))},
            "resuming",
        ),
        ({"resumed_at": iso(hours_ago(2))}, "resuming"),
    ],
)
def test_a_source_that_cannot_capture_is_suppressed(state: Path, doc: dict, reason: str) -> None:
    heartbeat(state)
    write_state(state, SOURCE_DOC, doc)
    assert suppression_reason(state, "whoop", NOW) == reason


# @spec CHECKS-SUPP-001
def test_suppression_ends_once_a_source_unit_succeeds_after_resuming(state: Path) -> None:
    heartbeat(state)
    write_state(
        state,
        SOURCE_DOC,
        {
            "cooldown_until": iso(hours_ago(2)),
            "resumed_at": iso(hours_ago(2)),
            "last_source_success_at": iso(hours_ago(1)),
        },
    )
    assert suppression_reason(state, "whoop", NOW) is None


# @spec CHECKS-SUPP-001
def test_a_capture_pause_suppresses_every_source(state: Path) -> None:
    heartbeat(state, pause={"reason": "disk full"})
    assert suppression_reason(state, "whoop", NOW) == "capture_paused"


# @spec CHECKS-SUPP-001
@pytest.mark.parametrize("beat", ["stale", "missing", "unreadable"])
def test_without_a_running_daemon_nothing_is_suppressed(state: Path, beat: str) -> None:
    write_state(state, SOURCE_DOC, {"state": "waiting_for_auth"})
    if beat == "stale":
        heartbeat(state, age=121, pause={"reason": "disk full"})
    elif beat == "unreadable":
        write_state(state, "heartbeat.json", "{")
    assert suppression_reason(state, "whoop", NOW) is None
