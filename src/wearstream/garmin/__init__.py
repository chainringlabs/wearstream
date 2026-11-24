"""The Garmin source: sign-in through `garminconnect`, daily and reference capture, and its
checks (Garmin design). Importing it never imports the library."""

from __future__ import annotations

from typing import Any

from wearstream.sources import REGISTRY


class GarminSource:
    """What the supervisor and the checks component ask of the Garmin source."""

    # @spec GARMIN-CAPTURE-JOB-001
    def collections(self, job: str, values: dict[str, Any]) -> tuple[str, ...]:
        """For backfill: every selected collection a `daily` unit writes."""
        from wearstream.garmin import catalog

        if job != "daily":
            return ()
        names = [e.name for e in catalog.CATALOG if e.kind in catalog.DAILY_KINDS]
        names += catalog.fan_out_collections(bool(values.get("WEARSTREAM_GARMIN_ALT_FORMATS")))
        return tuple(n for n in names if catalog.selected(n, values))

    def fingerprint(self, paths: Any) -> str:
        import os

        from wearstream.garmin.store import fingerprint

        os.environ.pop("GARMINTOKENS", None)  # the daemon applies the units' rule too
        return fingerprint(paths.secrets_path("garmin"))

    def check_settings(self, values: dict[str, Any]) -> list[Any]:
        from wearstream.garmin.checks import check_settings

        return check_settings(values)

    def own_checks(self, values: dict[str, Any], paths: Any) -> list[Any]:
        from wearstream.garmin.checks import tokenstore_health

        return [tokenstore_health(paths)]

    # @spec GARMIN-CAPTURE-CHECKS-007
    def run(self, ctx: Any) -> None:
        if ctx.job.kind == "checks":
            from wearstream.checks import run_unit as run_checks
            from wearstream.garmin.library import prepare

            prepare(ctx.values, ctx.log)
            run_checks(ctx, self.check_settings(ctx.values), self.own_checks(ctx.values, ctx.paths))
            return
        from wearstream.garmin.units import run_unit

        run_unit(ctx)


REGISTRY["garmin"] = GarminSource()
