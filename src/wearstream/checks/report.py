"""Results in `/state/checks/<source>.json`, and the events their changes log
(checks design, *Reporting*)."""

from __future__ import annotations

import datetime as dt
from typing import Any

from wearstream.checks.result import FAILING, Result
from wearstream.state import StateStore, iso, parse_time

REMIND_AFTER = dt.timedelta(hours=24)
# The details that identify a finding; others (an age, a count) may change between runs.
KEY_DETAILS = ("new_keys", "missing_keys", "files", "classes", "gaps", "error_type")
Row = dict[str, Any]


def _key(details: Any) -> dict[str, Any]:
    details = details if isinstance(details, dict) else {}
    return {k: details[k] for k in KEY_DETAILS if k in details}


def _due(row: Row, now: dt.datetime) -> bool:
    last = parse_time(row.get("last_logged"))
    return last is None or now - last >= REMIND_AFTER


class Reporter:
    """Compares each new result with the stored one, logs the change, then saves the result.

    Only a source's checks lock holder writes the document, so it is read once and kept."""

    def __init__(self, store: StateStore, source: str, logger: Any, *, manual: bool = False):
        self.store, self.source, self.log, self.manual = store, source, logger, manual
        self.relative = f"checks/{source}.json"
        self._rows: dict[str, Row] | None = None

    @property
    def rows(self) -> dict[str, Row]:
        if self._rows is None:
            doc = self.store.read(self.relative) or {}
            found = doc.get("checks")
            self._rows = dict(found) if isinstance(found, dict) else {}
        return self._rows

    def _save(self) -> None:
        self.store.write(self.relative, {"checks": self.rows})

    def _fields(self, name: str, row: Row) -> dict[str, Any]:
        fields: dict[str, Any] = {"source": self.source, "check": name}
        if row.get("collection"):
            fields.update(collection=row["collection"], family=row["family"])
        return fields

    def _log_failing(self, name: str, row: Row, result: Result, **extra: Any) -> None:
        fields = {**self._fields(name, row), "status": result.status, "details": result.details}
        if "error_type" in result.details:
            fields["error_type"] = result.details["error_type"]
        if result.severity == "error":
            self.log.error("check_failed", action=result.action or "", **fields, **extra)
        else:
            self.log.warning("check_warning", **fields, **extra, **_action(result))

    # @spec CHECKS-REPORT-001, CHECKS-REPORT-002, CHECKS-REPORT-003, CHECKS-REPORT-004
    # @spec CHECKS-REPORT-005, CHECKS-REPORT-007, CHECKS-REPORT-009
    def record(
        self,
        name: str,
        result: Result,
        now: dt.datetime,
        *,
        collection: str | None = None,
        family: str | None = None,
    ) -> None:
        """Log what changed, then save the row (an event is never lost to a kill between)."""
        old = self.rows.get(name) or {}
        row: Row = {
            **old,
            "kind": "collection" if collection else "own",
            "collection": collection,
            "family": family,
        }
        if result.suppressed:
            if not old.get("suppressed"):
                self.log.info(
                    "check_suppressed", reason=result.suppressed, **self._fields(name, row)
                )
            row["suppressed"] = result.suppressed
        else:
            if old.get("suppressed"):
                self.log.info("check_unsuppressed", **self._fields(name, row))
            row = self._compare(name, old, row, result, now)
        self.rows[name] = row
        self._save()

    def _compare(self, name: str, old: Row, row: Row, result: Result, now: dt.datetime) -> Row:
        same_finding = (old.get("severity"), old.get("status")) == (result.severity, result.status)
        same = same_finding and _key(old.get("details")) == _key(result.details)
        new = {
            **row,
            "severity": result.severity,
            "status": result.status,
            "details": result.details,
            "action": result.action,
            "suppressed": None,
            "since": old.get("since") if (same if result.failing else same_finding) else iso(now),
            "last_logged": old.get("last_logged"),
        }
        new.pop("accepted", None)
        # A hand-started run's events reach the operator's terminal, not the container's log.
        if not self.manual:
            logged = iso(now)
        else:
            logged = old.get("last_logged") if same else None
        if result.failing and not same:
            self._log_failing(name, row, result)
            new["last_logged"] = logged
        elif result.failing and _due(old, now):
            self._log_failing(name, row, result, since=old.get("since"))
            new["last_logged"] = logged
        elif not result.failing and old.get("severity") in FAILING:
            self.log.info("check_recovered", **self._fields(name, row))
        return new

    # @spec CHECKS-REPORT-006
    def remove_absent(self, names: set[str]) -> None:
        """Drop the rows of collection checks no longer in the source's settings."""
        gone = [
            n for n, row in self.rows.items() if row.get("kind") == "collection" and n not in names
        ]
        for name in gone:
            del self.rows[name]
            self.log.info("check_removed", source=self.source, check=name)
        if gone:
            self._save()


def _action(result: Result) -> dict[str, str]:
    return {"action": result.action} if result.action else {}
