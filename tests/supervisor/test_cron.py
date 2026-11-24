"""The cron evaluator behind every schedule (SUPERVISOR-SCHED-001, -JOB-003)."""

from __future__ import annotations

import datetime as dt

import pytest

from wearstream.cron import Cron

UTC = dt.UTC


def at(text: str) -> dt.datetime:
    return dt.datetime.fromisoformat(text).replace(tzinfo=UTC)


# @spec SUPERVISOR-SCHED-001
@pytest.mark.parametrize(
    ("expression", "after", "expected"),
    [
        ("17 * * * *", "2026-11-23T10:00", "2026-11-23T10:17"),
        ("17 * * * *", "2026-11-23T10:17", "2026-11-23T11:17"),
        ("30 5 * * *", "2026-11-23T05:30", "2026-11-24T05:30"),
        ("7 3-21/6 * * *", "2026-11-23T03:08", "2026-11-23T09:07"),
        ("7 3-21/6 * * *", "2026-11-23T21:08", "2026-11-24T03:07"),
        ("5 1 1 * *", "2026-11-23T00:00", "2026-12-01T01:05"),
        ("5 1 * * 0", "2026-11-23T00:00", "2026-11-29T01:05"),
        ("5 1 * * 7", "2026-11-23T00:00", "2026-11-29T01:05"),
        ("5 1 1 * 1", "2026-11-24T00:00", "2026-11-30T01:05"),
        ("5 1 29 2 *", "2026-03-01T00:00", "2028-02-29T01:05"),
        ("9 */12 * 1,7 *", "2026-11-23T00:00", "2027-01-01T00:09"),
    ],
)
def test_next_fire_time_is_strictly_after_in_utc(
    expression: str, after: str, expected: str
) -> None:
    assert Cron(expression).next_after(at(after)) == at(expected)


# @spec SUPERVISOR-SCHED-001
def test_fire_times_are_utc_whatever_the_input_offset() -> None:
    eastern = dt.datetime(2026, 11, 23, 5, 0, tzinfo=dt.timezone(dt.timedelta(hours=-5)))
    assert Cron("17 * * * *").next_after(eastern) == at("2026-11-23T10:17")


# @spec SUPERVISOR-SCHED-001
def test_latest_fire_at_or_before() -> None:
    cron = Cron("17 * * * *")
    assert cron.latest_at_or_before(at("2026-11-23T10:17")) == at("2026-11-23T10:17")
    assert cron.latest_at_or_before(at("2026-11-23T10:16")) == at("2026-11-23T09:17")


# @spec SUPERVISOR-JOB-003
@pytest.mark.parametrize(
    ("expression", "interval_h", "longest_h"),
    [
        ("17 * * * *", 1, 1),
        ("30 5 * * *", 24, 24),
        ("7 3-21/6 * * *", 6, 6),
        ("7 3,4 * * *", 1, 23),
        ("17 * * * 1", 1, 24 * 6 + 1),
    ],
)
def test_interval_and_longest_gap(expression: str, interval_h: int, longest_h: int) -> None:
    cron = Cron(expression)
    assert cron.interval == dt.timedelta(hours=interval_h)
    assert cron.longest_gap == dt.timedelta(hours=longest_h)


@pytest.mark.parametrize(
    "expression",
    [
        "17 24 * * *",
        "60 * * * *",
        "1 * 0 * *",
        "1 * * 13 *",
        "1 * 30 2 *",
        "1 * * * 8",
        "1 5-2 * * *",
        "1 */0 * * *",
    ],
)
def test_impossible_or_out_of_range_schedules_are_rejected(expression: str) -> None:
    with pytest.raises(ValueError):
        Cron(expression)
