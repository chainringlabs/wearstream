"""The Whoop source: the token lifecycle, range and snapshot capture, and its checks
(Whoop design)."""

from __future__ import annotations

from typing import Any

from wearstream.sources import REGISTRY


class WhoopSource:
    """What the supervisor and the checks component ask of the Whoop source."""

    def collections(self, job: str, values: dict[str, Any]) -> tuple[str, ...]:
        from wearstream.whoop.capture import RANGE_COLLECTIONS

        return RANGE_COLLECTIONS if job == "range" else ()

    def fingerprint(self, paths: Any) -> str:
        from wearstream.whoop.files import fingerprint

        return fingerprint(paths.secrets_path("whoop"))

    def check_settings(self, values: dict[str, Any]) -> list[Any]:
        from wearstream.whoop.checks import check_settings

        return check_settings(values)

    def own_checks(self, values: dict[str, Any], paths: Any) -> list[Any]:
        from wearstream.whoop.checks import token_health

        return [token_health(values, paths)]

    # @spec WHOOP-CAPTURE-CHECKS-004
    def run(self, ctx: Any) -> None:
        if ctx.job.kind == "checks":
            from wearstream.checks import run_unit as run_checks

            run_checks(ctx, self.check_settings(ctx.values), self.own_checks(ctx.values, ctx.paths))
            return
        from wearstream.whoop.capture import run_unit

        run_unit(ctx)


REGISTRY["whoop"] = WhoopSource()
