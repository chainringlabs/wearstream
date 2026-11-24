"""Content health: sampled payloads carry data and match their sidecars
(CHECKS-CONTENT-001..006)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from checks_helpers import days_back, put, setting

from wearstream.checks import content_health


def content(**fields: object) -> object:
    return setting("sleep", "content", **fields)


def corrupt(payload: Path) -> None:
    payload.write_bytes(payload.read_bytes() + b" ")


# @spec CHECKS-CONTENT-001
def test_samples_the_newest_and_an_even_spread_of_the_rest(data: Path) -> None:
    paths = [put(data, "sleep", day, {"day": day}) for day in days_back(9)]
    result = content_health(data, content())
    assert (result.severity, result.details["sampled"]) == ("pass", 5)
    corrupt(paths[1])  # between the first and third of the rest: never sampled
    assert content_health(data, content()).severity == "pass"
    for sampled in (paths[0], paths[8]):
        corrupt(sampled)
        result = content_health(data, content())
        assert result.severity == "error"
        assert str(sampled) in result.details["files"]


# @spec CHECKS-CONTENT-001
def test_fewer_than_five_captures_are_all_sampled(data: Path) -> None:
    for day in days_back(3):
        put(data, "sleep", day, {"a": 1})
    assert content_health(data, content()).details["sampled"] == 3


# @spec CHECKS-CONTENT-002
@pytest.mark.parametrize("damage", ["bytes", "size"])
def test_a_payload_that_no_longer_matches_its_sidecar_is_corrupted(data: Path, damage: str) -> None:
    payload = put(data, "sleep", "2026-11-23", {"a": 1})
    if damage == "bytes":
        payload.write_bytes(b'{"a": 2}')
    else:
        sidecar = payload.with_name(payload.name.replace(".json", ".meta.json"))
        meta = json.loads(sidecar.read_text())
        sidecar.write_text(json.dumps({**meta, "byte_size": meta["byte_size"] + 1}))
    result = content_health(data, content(expected_empty=True))
    assert (result.severity, result.status) == ("error", "corrupted")
    assert result.details["files"] == [str(payload)]
    assert "backup" in result.action and "aside" in result.action
    assert "won't be re-captured" in result.action


# @spec CHECKS-CONTENT-002, CHECKS-CONTENT-003
def test_zero_byte_payloads(data: Path) -> None:
    put(data, "sleep", "2026-11-23", b"")
    assert content_health(data, content()).severity == "error"
    put(data, "hr", "2026-11-23", b"", ext="csv")
    result = content_health(data, setting("hr", "content", reader="csv"))
    assert result.severity == "warning"


# @spec CHECKS-CONTENT-004
@pytest.mark.parametrize(
    ("payload", "kind"),
    [
        ([], "EMPTY_LIST"),
        ({}, "EMPTY_OBJECT"),
        ({"records": [], "next_token": None}, "EMPTY_WRAPPER"),
        (None, "NULL"),
        ({"error": "rate limited", "message": "slow down"}, "ERROR_LIKE"),
        (b"{nope", "UNPARSEABLE"),
    ],
)
def test_empty_error_like_and_unparseable_samples_warn(
    data: Path, payload: object, kind: str
) -> None:
    put(data, "sleep", "2026-11-23", payload)
    result = content_health(data, content())
    assert result.severity == "warning"
    assert kind in result.details["classes"]


# @spec CHECKS-CONTENT-004
def test_a_wrapper_with_data_or_other_content_is_data(data: Path) -> None:
    put(data, "sleep", "2026-11-22", {"records": [{"a": 1}], "error": None})
    put(data, "sleep", "2026-11-23", {"records": [], "summary": {"total": 0}})
    assert content_health(data, content()).severity == "pass"


# @spec CHECKS-CONTENT-004
def test_an_http_error_sidecar_warns(data: Path) -> None:
    put(data, "sleep", "2026-11-23", {"a": 1}, meta={"http_status": 503})
    result = content_health(data, content())
    assert result.severity == "warning"
    assert "HTTP_ERROR" in result.details["classes"]


# @spec CHECKS-CONTENT-004
def test_expected_empty_collections_pass_on_empties_but_not_errors(data: Path) -> None:
    put(data, "sleep", "2026-11-22", [])
    put(data, "sleep", "2026-11-23", None)
    assert content_health(data, content(expected_empty=True)).severity == "pass"
    put(data, "sleep", "2026-11-24", {"errors": ["bad"]})
    assert content_health(data, content(expected_empty=True)).severity == "warning"


# @spec CHECKS-CONTENT-005
def test_nothing_to_sample_passes(data: Path) -> None:
    assert content_health(data, content()).severity == "pass"


# @spec CHECKS-CONTENT-006
def test_an_extension_that_disagrees_with_the_reader_warns(data: Path) -> None:
    put(data, "sleep", "2026-11-23", b"a,b\n1,2\n", ext="csv")
    result = content_health(data, content())
    assert (result.severity, result.status) == ("warning", "reader_mismatch")
    assert "reader" in result.details["setting"]
    assert "check settings" in result.action
