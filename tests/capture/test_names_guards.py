"""Name validation and the guards, in order (CAPTURE-NAME, CAPTURE-GUARD)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from capture_helpers import DAY, META, Logs, tree

from wearstream.capture import capture


def _call(root: Path, **overrides: Any) -> Any:
    args: dict[str, Any] = {
        "source": "whoop",
        "collection": "sleep",
        "payload": b"{}",
        "meta": META,
    }
    args.update(overrides)
    kwargs = {k: args.pop(k) for k in ("dt", "ext", "dedupe", "dedupe_scope") if k in args}
    kwargs.setdefault("dt", DAY)
    return capture(
        root, args["source"], args["collection"], args["payload"], args["meta"], **kwargs
    )


# @spec CAPTURE-NAME-001
@pytest.mark.parametrize(
    "bad",
    [
        {"source": "Whoop"},
        {"source": "who op"},
        {"source": "../etc"},
        {"source": ""},
        {"collection": "sleep/naps"},
        {"collection": "Sleep"},
        {"dt": "2026-02-30"},
        {"dt": "2026-1-1"},
        {"dt": "20261120"},
        {"dt": "../.."},
        {"ext": "JSON"},
        {"ext": ".json"},
        {"ext": "json."},
        {"ext": "fit..zip"},
        {"ext": "json/x"},
    ],
)
def test_invalid_names_write_nothing(root: Path, bad: dict[str, str]) -> None:
    assert _call(root, **bad).outcome == "failed"
    assert tree(root) == []


# @spec CAPTURE-NAME-001
@pytest.mark.parametrize(
    "good", [{"collection": "daily_summary"}, {"collection": "hrv-2"}, {"ext": "fit.zip"}]
)
def test_valid_names_are_written(root: Path, good: dict[str, str]) -> None:
    assert _call(root, **good).outcome == "written"


# @spec CAPTURE-NAME-002
@pytest.mark.parametrize("ext", ["meta.json", "x.meta.json"])
def test_an_extension_that_would_look_like_a_sidecar_is_refused(root: Path, ext: str) -> None:
    assert _call(root, ext=ext).outcome == "failed"
    assert tree(root) == []


# @spec CAPTURE-GUARD-001
@pytest.mark.parametrize("payload", ["{}", None, memoryview(b"{}"), {"a": 1}])
def test_payloads_must_be_bytes(root: Path, payload: object) -> None:
    assert _call(root, payload=payload).outcome == "failed"
    assert tree(root) == []


# @spec CAPTURE-GUARD-001
def test_bytearray_payloads_are_accepted(root: Path) -> None:
    result = _call(root, payload=bytearray(b"{}"))
    assert result.outcome == "written" and Path(result.path).read_bytes() == b"{}"


# @spec CAPTURE-GUARD-002
@pytest.mark.parametrize("status", ["200", 200.0, True])
def test_a_status_that_is_not_an_integer_fails(root: Path, status: object) -> None:
    assert _call(root, meta={**META, "http_status": status}).outcome == "failed"
    assert tree(root) == []


# @spec CAPTURE-GUARD-003
@pytest.mark.parametrize("status", [199, 300, 404, 500])
def test_a_non_2xx_status_is_refused_and_logged(root: Path, logs: Logs, status: int) -> None:
    assert _call(root, meta={**META, "http_status": status}).outcome == "skipped_non_2xx"
    assert tree(root) == []
    [event] = logs.events("capture_skipped_non_2xx")
    assert (event["level"], event["http_status"]) == ("warning", status)


# @spec CAPTURE-GUARD-003
@pytest.mark.parametrize(
    "meta", [{**META, "http_status": 204}, {"content_type": "application/json"}]
)
def test_2xx_or_no_status_is_written(root: Path, meta: dict[str, Any]) -> None:
    assert _call(root, meta=meta).outcome == "written"


# @spec CAPTURE-GUARD-004
def test_guards_run_names_then_type_then_status_then_skip_identical(root: Path, logs: Logs) -> None:
    assert _call(root, source="Bad", payload="text").outcome == "failed"
    assert "source" in logs.events("capture_failed")[-1]["error"]
    assert _call(root, payload="text", meta={**META, "http_status": 404}).outcome == "failed"
    assert _call(root).outcome == "written"
    assert _call(root, meta={**META, "http_status": "200"}).outcome == "failed"
    assert _call(root, meta={**META, "http_status": 404}).outcome == "skipped_non_2xx"
    assert _call(root).outcome == "skipped_identical"
