"""The `range` unit and pagination (WHOOP-CAPTURE-RANGE-001..006, WHOOP-CAPTURE-PAGE-001..004,
WHOOP-AUTH-REFRESH-008, -013, WHOOP-CAPTURE-REPORT-001, -003)."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from whoop_helpers import FakeClock, FakeWhoop, Logs, minutes, page, read_token, reply, write_token

from wearstream.capture import list_captures

Run = Callable[..., tuple[int, dict[str, Any]]]
DAY = "2026-11-20"
ORDER = ["sleep", "recovery", "workout", "cycle"]


def stored(data: Path, collection: str, day: str = DAY) -> list[Any]:
    return list_captures(data, "whoop", collection, day)


# @spec WHOOP-CAPTURE-RANGE-001
def test_the_four_collections_are_asked_for_the_utc_day_in_order(
    token: dict, whoop: FakeWhoop, run: Run
) -> None:
    assert run(day=DAY)[0] == 0
    assert whoop.collections_requested() == ORDER
    for request in whoop.data_requests():
        assert request.url.params["start"] == "2026-11-20T00:00:00.000Z"
        assert request.url.params["end"] == "2026-11-21T00:00:00.000Z"
        assert request.headers["authorization"] == "Bearer AT-0"


# @spec WHOOP-CAPTURE-RANGE-002
def test_a_page_with_records_is_captured_into_the_units_day(
    token: dict, whoop: FakeWhoop, run: Run, data: Path
) -> None:
    run(day=DAY)
    [capture] = stored(data, "sleep")
    assert json.loads(capture.payload.read_bytes())["records"][0]["id"] == "sleep-1"
    meta = capture.meta
    assert meta["request_url"].startswith("https://api.prod.whoop.com/developer/v2/activity/sleep?")
    assert meta["request_params"] == {
        "start": "2026-11-20T00:00:00.000Z",
        "end": "2026-11-21T00:00:00.000Z",
        "limit": "25",
    }
    assert "AT-0" not in json.dumps(meta)


# @spec WHOOP-CAPTURE-RANGE-002
def test_an_unchanged_page_is_skipped(token: dict, whoop: FakeWhoop, run: Run, data: Path) -> None:
    run(day=DAY)
    run(day=DAY)
    assert len(stored(data, "cycle")) == 1


# @spec WHOOP-CAPTURE-RANGE-003, WHOOP-CAPTURE-PAGE-002
def test_empty_pages_are_not_captured(token: dict, whoop: FakeWhoop, run: Run, data: Path) -> None:
    whoop.script("workout", reply(200, page(next_token="ignored")))
    assert run(day=DAY)[0] == 0
    assert stored(data, "workout") == []
    assert len(whoop.data_requests("workout")) == 1


# @spec WHOOP-CAPTURE-RANGE-004, WHOOP-CAPTURE-RANGE-006, WHOOP-CAPTURE-REPORT-001
def test_a_failing_collection_does_not_stop_the_others(
    token: dict, whoop: FakeWhoop, run: Run, data: Path
) -> None:
    whoop.script("recovery", reply(404, {"error": "SECRET-BODY"}))
    code, result = run(day=DAY)
    assert code == 1
    assert "recovery" in result["error"]
    assert stored(data, "recovery") == []
    assert [c for c in ORDER if stored(data, c)] == ["sleep", "workout", "cycle"]
    assert len(whoop.data_requests("recovery")) == 1


# @spec WHOOP-CAPTURE-RANGE-005
def test_a_2xx_body_that_is_not_json_fails_without_retry_or_capture(
    token: dict, whoop: FakeWhoop, run: Run, data: Path
) -> None:
    whoop.script("sleep", reply(200, b"<html>maintenance</html>"))
    assert run(day=DAY)[0] == 1
    assert stored(data, "sleep") == []
    assert len(whoop.data_requests("sleep")) == 1


# @spec WHOOP-CAPTURE-PAGE-001
def test_pages_follow_next_token_with_limit_25(
    token: dict, whoop: FakeWhoop, run: Run, data: Path
) -> None:
    whoop.script(
        "cycle",
        reply(200, page({"id": 1}, next_token="A")),
        reply(200, page({"id": 2}, next_token="B")),
        reply(200, page({"id": 3})),
    )
    run(day=DAY)
    asked = whoop.data_requests("cycle")
    assert [r.url.params.get("nextToken") for r in asked] == [None, "A", "B"]
    assert {r.url.params["limit"] for r in asked} == {"25"}
    assert len(stored(data, "cycle")) == 3
    second = sorted(stored(data, "cycle"), key=lambda c: c.ms)[1]
    assert second.meta["request_params"]["nextToken"] == "A"


# @spec WHOOP-CAPTURE-PAGE-003
@pytest.mark.parametrize("tokens", [["A", "A"], ["A", "B", "A"]])
def test_a_cursor_that_repeats_ends_pagination_successfully(
    token: dict, whoop: FakeWhoop, run: Run, logs: Logs, tokens: list[str]
) -> None:
    whoop.script(
        "cycle",
        *[reply(200, page({"id": i}, next_token=t)) for i, t in enumerate(tokens)],
    )
    assert run(day=DAY)[0] == 0
    assert [r.url.params.get("nextToken") for r in whoop.data_requests("cycle")] == [
        None,
        *tokens[:-1],
    ]
    assert any(e["level"] == "warning" for e in logs.events() if "cycle" in json.dumps(e))


# @spec WHOOP-CAPTURE-PAGE-004
def test_twenty_pages_end_pagination_successfully(
    token: dict, whoop: FakeWhoop, run: Run, data: Path, logs: Logs
) -> None:
    counter = iter(range(1000))

    def endless(_request: httpx.Request) -> httpx.Response:
        n = next(counter)
        return reply(200, page({"id": n}, next_token=f"T{n}"))

    whoop.script("cycle", endless)
    assert run(day=DAY, time_limit=3600)[0] == 0
    assert len(whoop.data_requests("cycle")) == 20
    assert len(stored(data, "cycle")) == 20
    assert "warning" in {e["level"] for e in logs.events() if e.get("collection") == "cycle"}


# @spec WHOOP-AUTH-REFRESH-013
def test_a_401_marks_the_token_expired_and_fails_without_retry(
    folder: Path, clock: FakeClock, whoop: FakeWhoop, run: Run
) -> None:
    write_token(folder, clock)
    whoop.script("sleep", reply(401))
    code, result = run(day=DAY)
    assert code == 1
    assert len(whoop.data_requests("sleep")) == 1
    assert read_token(folder)["expires_at"] <= minutes(clock, 0)
    assert whoop.collections_requested() == ["sleep"] and whoop.token_forms() == []
    assert all(collection in result["error"] for collection in ORDER)


# @spec WHOOP-AUTH-REFRESH-008, WHOOP-CAPTURE-REPORT-003
def test_a_token_failure_is_never_retried_and_fails_every_collection(
    folder: Path, clock: FakeClock, whoop: FakeWhoop, run: Run
) -> None:
    write_token(folder, clock, refreshed_at=minutes(clock, -45))
    whoop.script("/oauth/oauth2/token", httpx.ReadTimeout("slow"))
    code, result = run(day=DAY)
    assert code == 1
    assert len(whoop.token_forms()) == 1
    assert whoop.data_requests() == []
    for collection in ORDER:
        assert collection in result["error"]
