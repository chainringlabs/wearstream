"""Checks over the capture tree: freshness, completeness, schema drift and content health,
run by each source's `checks` job and reported through the log (checks design)."""

from wearstream.checks.completeness import completeness
from wearstream.checks.content import content_health
from wearstream.checks.freshness import freshness
from wearstream.checks.report import Reporter
from wearstream.checks.result import OwnCheck, Result
from wearstream.checks.schema import SIGNATURE_VERSION, schema_drift
from wearstream.checks.settings import CheckSetting, check_names, setting_problems
from wearstream.checks.suppress import suppression_reason
from wearstream.checks.unit import run_unit

__all__ = [
    "SIGNATURE_VERSION",
    "CheckSetting",
    "OwnCheck",
    "Reporter",
    "Result",
    "check_names",
    "completeness",
    "content_health",
    "freshness",
    "run_unit",
    "schema_drift",
    "setting_problems",
    "suppression_reason",
]
