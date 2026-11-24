"""Completeness: gaps in the event timeline (CHECKS-COMPLETE-001..005, CHECKS-SUPP-001, -003)."""

from __future__ import annotations

from pathlib import Path

import pytest
from checks_helpers import NOW, days_back, put, setting

from wearstream.checks import completeness

PARTITION = {"event_date_source": "partition"}


def by_partition(**fields: object) -> object:
    return setting("sleep", "completeness", **PARTITION, **fields)


def by_field(name: str = "start", **fields: object) -> object:
    return setting(
        "cycle", "completeness", event_date_source="payload", event_date_field=name, **fields
    )


# @spec CHECKS-COMPLETE-003
def test_one_missing_day_passes_and_two_warn(data: Path) -> None:
    for day in ("2026-11-18", "2026-11-20"):
        put(data, "sleep", day, [{"a": 1}])
    assert completeness(data, by_partition(), NOW).severity == "pass"
    put(data, "sleep", "2026-11-23", [{"a": 1}])
    result = completeness(data, by_partition(), NOW)
    assert (result.severity, result.status) == ("warning", "gaps")
    assert result.details["earliest"] == "2026-11-18"
    assert result.details["latest"] == "2026-11-23"
    assert result.details["event_days"] == 3
    assert result.details["gaps"] == [["2026-11-20", "2026-11-23"]]
    assert result.details["biggest_gap_days"] == 3


# @spec CHECKS-COMPLETE-001
def test_empty_payloads_give_no_event_date(data: Path) -> None:
    put(data, "sleep", "2026-11-18", [{"a": 1}])
    for day, empty in (("2026-11-19", []), ("2026-11-20", {}), ("2026-11-21", {"records": []})):
        put(data, "sleep", day, empty)
    put(data, "sleep", "2026-11-22", [{"a": 1}])
    result = completeness(data, by_partition(unnest_records=False), NOW)
    assert result.details["gaps"] == [["2026-11-18", "2026-11-22"]]


# @spec CHECKS-COMPLETE-001
def test_payload_dates_come_from_each_record_or_list_element(data: Path) -> None:
    put(data, "cycle", "2026-11-23", [{"start": "2026-11-10T22:00:00Z"}, {"start": "2026-11-11"}])
    put(data, "cycle", "2026-11-24", {"start": "2026-11-12T08:00:00.000-05:00"})
    result = completeness(data, by_field(), NOW)
    assert (result.details["earliest"], result.details["latest"]) == ("2026-11-10", "2026-11-12")
    assert result.severity == "pass"


# @spec CHECKS-COMPLETE-001
def test_unnest_records_reads_the_wrapper_records_and_dotted_fields(data: Path) -> None:
    put(
        data,
        "cycle",
        "2026-11-23",
        {"records": [{"during": {"start": "2026-11-01"}}, {"during": {"start": "2026-11-05"}}]},
    )
    result = completeness(data, by_field("during.start", unnest_records=True), NOW)
    assert result.details["gaps"] == [["2026-11-01", "2026-11-05"]]


# @spec CHECKS-COMPLETE-001
def test_csv_rows_give_their_column(data: Path) -> None:
    rows = b"date,value\n2026-11-01,1\n2026-11-02,2\n2026-11-09,3\n"
    put(data, "cycle", "2026-11-23", rows, ext="csv", meta={"content_type": "text/csv"})
    result = completeness(data, by_field("date", reader="csv"), NOW)
    assert result.details["gaps"] == [["2026-11-02", "2026-11-09"]]


# @spec CHECKS-COMPLETE-002
@pytest.mark.parametrize(
    ("value", "day"),
    [
        ("2026-11-03T23:30:00-08:00", "2026-11-03"),
        ("2026-11-03", "2026-11-03"),
        ("11/03/2026", "2026-11-03"),
        ("11-03-2026", "2026-11-03"),
        ("2026/11/03", "2026-11-03"),
        (1762214400, "2025-11-04"),
        (1762214400.5, "2025-11-04"),
    ],
)
def test_event_date_formats(data: Path, value: object, day: str) -> None:
    put(data, "cycle", "2026-11-23", [{"start": value}])
    assert completeness(data, by_field(), NOW).details["earliest"] == day


# @spec CHECKS-COMPLETE-002
def test_values_that_do_not_parse_are_ignored(data: Path) -> None:
    put(data, "cycle", "2026-11-23", [{"start": "yesterday"}, {"start": None}, {"other": 1}])
    put(data, "cycle", "2026-11-24", [{"start": "2026-11-20"}, {"start": "2026-11-21"}])
    result = completeness(data, by_field(), NOW)
    assert result.details["event_days"] == 2


# @spec CHECKS-COMPLETE-004
def test_gap_to_now_warns_on_a_stale_newest_date_even_alone(data: Path) -> None:
    put(data, "sleep", "2026-11-21", [{"a": 1}])
    assert completeness(data, by_partition(gap_to_now=True), NOW).severity == "warning"
    put(data, "sleep", "2026-11-22", [{"a": 1}])
    assert completeness(data, by_partition(gap_to_now=True), NOW).severity == "pass"


# @spec CHECKS-COMPLETE-004
def test_gap_to_now_with_only_empty_captures_warns_no_recent_event(data: Path) -> None:
    put(data, "sleep", "2026-11-24", [])
    result = completeness(data, by_partition(gap_to_now=True), NOW)
    assert (result.severity, result.status) == ("warning", "no_recent_event")


# @spec CHECKS-COMPLETE-004
def test_gap_to_now_with_no_captures_at_all_passes(data: Path) -> None:
    assert completeness(data, by_partition(gap_to_now=True), NOW).severity == "pass"


# @spec CHECKS-COMPLETE-005
def test_without_gap_to_now_fewer_than_two_dates_pass(data: Path) -> None:
    put(data, "sleep", "2026-10-01", [{"a": 1}])
    result = completeness(data, by_partition(), NOW)
    assert result.severity == "pass"


# @spec CHECKS-COMPLETE-003
def test_only_the_trailing_folders_count(data: Path) -> None:
    for day in ["2026-10-01", *days_back(3)]:
        put(data, "sleep", day, [{"a": 1}])
    assert completeness(data, by_partition(recent_folders=3), NOW).severity == "pass"
    assert completeness(data, by_partition(recent_folders=4), NOW).severity == "warning"


# @spec CHECKS-SUPP-001, CHECKS-SUPP-003
def test_suppression_covers_the_gap_to_now_term_but_not_gaps_inside(data: Path) -> None:
    put(data, "sleep", "2026-11-19", [{"a": 1}])
    put(data, "sleep", "2026-11-20", [{"a": 1}])
    quiet = completeness(data, by_partition(gap_to_now=True), NOW, suppressed="rate_limited")
    assert quiet.suppressed == "rate_limited"
    put(data, "sleep", "2026-11-15", [{"a": 1}])
    gaps = completeness(data, by_partition(gap_to_now=True), NOW, suppressed="rate_limited")
    assert (gaps.severity, gaps.status, gaps.suppressed) == ("warning", "gaps", None)
