"""Check settings: what a source declares per collection, their validation and check names."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

FAMILIES = ("freshness", "completeness", "schema", "content")
FAMILY_NAMES = {
    "freshness": "freshness",
    "completeness": "completeness",
    "schema": "schema_drift",
    "content": "content_health",
}
READERS = ("json", "csv", "txt")
MAX_RECENT_FOLDERS = 60


@dataclass(frozen=True)
class CheckSetting:
    source: str
    collection: str
    families: tuple[str, ...]
    reader: str = "json"
    unnest_records: bool = False
    event_date_source: str | None = None  # payload or partition
    event_date_field: str | None = None
    cadence_hours: float = 26
    grace_hours: float = 6
    cadence_days: int = 2
    gap_to_now: bool = False
    expected_empty: bool = False
    recent_folders: int = 14

    @property
    def tolerance_hours(self) -> float:
        return self.cadence_hours + self.grace_hours


def _problems(s: CheckSetting) -> list[str]:
    where = f"{s.source} {s.collection} check settings"
    found = [
        f"{where}: unknown check family {f!r} (use freshness, completeness, schema or content)"
        for f in s.families
        if f not in FAMILIES
    ]
    if "completeness" in s.families and s.event_date_source is None:
        found.append(f"{where}: completeness needs an event_date_source (payload or partition)")
    elif s.event_date_source not in (None, "payload", "partition"):
        found.append(f"{where}: event_date_source must be payload or partition")
    elif s.event_date_source == "payload" and not s.event_date_field:
        found.append(f"{where}: event_date_source payload needs an event_date_field")
    if not 1 <= s.recent_folders <= MAX_RECENT_FOLDERS:
        found.append(f"{where}: recent_folders must be from 1 to {MAX_RECENT_FOLDERS}")
    if s.reader not in READERS:
        found.append(f"{where}: reader must be json, csv or txt")
    return found


# @spec CHECKS-SET-001
def setting_problems(settings: list[CheckSetting]) -> list[str]:
    """Every problem with a source's check settings, each naming the source and collection."""
    return [problem for s in settings for problem in _problems(s)]


def source_setting_problems(values: dict[str, Any]) -> list[str]:
    """Problems with the check settings of every enabled source this build provides."""
    from wearstream.sources import get_source

    problems: list[str] = []
    for name in values.get("WEARSTREAM_SOURCES", []):
        try:
            declare = getattr(get_source(name), "check_settings", None)
        except LookupError:
            continue
        if declare is not None:
            problems += setting_problems(declare(values))
    return problems


# @spec CHECKS-SET-002
def check_name(s: CheckSetting, family: str) -> str:
    return f"{s.source}_{s.collection}_{FAMILY_NAMES.get(family, family)}"


def check_names(s: CheckSetting) -> list[str]:
    return [check_name(s, family) for family in s.families]
