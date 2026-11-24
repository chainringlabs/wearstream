"""Schema drift: the payload's top-level keys against the accepted baseline
(CHECKS-SCHEMA-001..006)."""

from __future__ import annotations

from pathlib import Path

from checks_helpers import put, setting, write_state

from wearstream.checks import SIGNATURE_VERSION, schema_drift

BASELINE = "checks/baselines/whoop/sleep.json"


def schema(**fields: object) -> object:
    return setting("sleep", "schema", **fields)


def baseline(state: Path, keys: list[str], version: int = SIGNATURE_VERSION) -> None:
    write_state(state, BASELINE, {"version": version, "keys": keys})


# @spec CHECKS-SCHEMA-001, CHECKS-SCHEMA-003
def test_no_baseline_records_the_keys_without_writing_them(data: Path, state: Path) -> None:
    put(data, "sleep", "2026-11-23", {"b": 1, "a": 2})
    result = schema_drift(data, state, schema())
    assert (result.severity, result.status) == ("pass", "baseline_set")
    assert result.baseline == {"version": SIGNATURE_VERSION, "keys": ["a", "b"]}
    assert not (state / BASELINE).exists()


# @spec CHECKS-SCHEMA-003
def test_an_empty_or_older_baseline_is_recorded_again(data: Path, state: Path) -> None:
    put(data, "sleep", "2026-11-23", {"a": 1, "z": 2})
    baseline(state, [])
    assert schema_drift(data, state, schema()).status == "baseline_set"
    baseline(state, ["a"], version=SIGNATURE_VERSION - 1)
    result = schema_drift(data, state, schema())
    assert (result.status, result.baseline["keys"]) == ("baseline_set", ["a", "z"])


# @spec CHECKS-SCHEMA-001
def test_keys_come_from_the_newest_non_empty_capture_of_each_folder(
    data: Path, state: Path
) -> None:
    put(data, "sleep", "2026-11-22", {"old": 1})
    put(data, "sleep", "2026-11-22", {"a": 1})
    put(data, "sleep", "2026-11-23", {"b": 1})
    put(data, "sleep", "2026-11-23", [])
    put(data, "sleep", "2026-11-23", b"not json")
    assert schema_drift(data, state, schema()).baseline["keys"] == ["a", "b"]


# @spec CHECKS-SCHEMA-001
def test_list_records_csv_and_text_signatures(data: Path, state: Path) -> None:
    put(data, "sleep", "2026-11-23", [{"a": 1}, {"b": 2}, 3])
    assert schema_drift(data, state, schema()).baseline["keys"] == ["a", "b"]
    put(data, "cycle", "2026-11-23", {"records": [{"x": 1}, {"y": 1}], "next_token": None})
    unnested = schema_drift(data, state, setting("cycle", "schema", unnest_records=True))
    assert unnested.baseline["keys"] == ["x", "y"]
    put(data, "hr", "2026-11-23", b"time,bpm\n1,60\n", ext="csv")
    assert schema_drift(data, state, setting("hr", "schema", reader="csv")).baseline["keys"] == [
        "bpm",
        "time",
    ]
    put(data, "obs", "2026-11-23", b"1  2 3\t4\n5 6\n", ext="txt")
    text = schema_drift(data, state, setting("obs", "schema", reader="txt"))
    assert text.baseline["keys"] == ["fields=4"]


# @spec CHECKS-SCHEMA-002
def test_no_readable_non_empty_payload_passes_no_payload(data: Path, state: Path) -> None:
    assert schema_drift(data, state, schema()).status == "no_payload"
    put(data, "sleep", "2026-11-23", [])
    put(data, "sleep", "2026-11-22", b"{broken")
    result = schema_drift(data, state, schema())
    assert (result.severity, result.status, result.baseline) == ("pass", "no_payload", None)


# @spec CHECKS-SCHEMA-004
def test_an_unreadable_baseline_is_an_error_asking_to_accept(data: Path, state: Path) -> None:
    put(data, "sleep", "2026-11-23", {"a": 1})
    write_state(state, BASELINE, "{not json")
    result = schema_drift(data, state, schema())
    assert (result.severity, result.status) == ("error", "baseline_unreadable")
    assert "wearstream accept-schema whoop sleep" in result.action
    assert result.baseline is None


# @spec CHECKS-SCHEMA-005
def test_a_new_key_is_drift(data: Path, state: Path) -> None:
    baseline(state, ["a", "b"])
    put(data, "sleep", "2026-11-23", {"a": 1, "b": 1, "signature": 1})
    result = schema_drift(data, state, schema())
    assert (result.severity, result.status) == ("error", "drift")
    assert result.details["new_keys"] == ["signature"]
    assert "wearstream accept-schema whoop sleep" in result.action
    assert result.baseline is None


# @spec CHECKS-SCHEMA-005, CHECKS-SCHEMA-006
def test_drift_outranks_missing_keys(data: Path, state: Path) -> None:
    baseline(state, ["a", "b"])
    put(data, "sleep", "2026-11-23", {"a": 1, "c": 1})
    assert schema_drift(data, state, schema()).status == "drift"


# @spec CHECKS-SCHEMA-006
def test_a_baseline_key_seen_in_no_payload_is_missing(data: Path, state: Path) -> None:
    baseline(state, ["a", "b", "c"])
    put(data, "sleep", "2026-11-22", {"a": 1})
    put(data, "sleep", "2026-11-23", {"a": 1, "b": 1})
    result = schema_drift(data, state, schema())
    assert (result.severity, result.status) == ("warning", "missing")
    assert result.details["missing_keys"] == ["c"]


# @spec CHECKS-SCHEMA-005
def test_keys_matching_the_baseline_pass(data: Path, state: Path) -> None:
    baseline(state, ["a", "b"])
    put(data, "sleep", "2026-11-22", {"a": 1})
    put(data, "sleep", "2026-11-23", {"b": 1})
    assert schema_drift(data, state, schema()).severity == "pass"
