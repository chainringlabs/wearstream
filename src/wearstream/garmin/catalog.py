"""The catalog: every Garmin library method WearStream may call, one entry per collection
(Garmin capture design, *The catalog and selection*). Importing it never imports the library."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

GOAL_STATUSES = ("active", "past", "future")
DAILY_KINDS = ("daily", "range", "per_device_range")
REFERENCE_KINDS = ("static", "static_goals", "weekly", "per_profile", "per_device")


@dataclass(frozen=True)
class Entry:
    name: str  # also the collection
    method: str
    kind: str
    skip_if_none: bool = False
    skip_if_empty: bool = False
    screen_secrets: bool = False

    @property
    def keeps_every_capture(self) -> bool:
        return self.skip_if_empty or self.skip_if_none


def _e(name: str, method: str, kind: str, flag: str = "") -> Entry:
    return Entry(name, method, kind, **({flag: True} if flag else {}))


E, N, S = "skip_if_empty", "skip_if_none", "screen_secrets"
CATALOG: tuple[Entry, ...] = (
    _e("user_summary", "get_user_summary", "daily"),
    _e("steps_intraday", "get_steps_data", "daily"),
    _e("floors", "get_floors", "daily"),
    _e("heart_rates", "get_heart_rates", "daily"),
    _e("resting_heart_rate", "get_rhr_day", "daily"),
    _e("body_battery_events", "get_body_battery_events", "daily"),
    _e("sleep", "get_sleep_data", "daily"),
    _e("stress", "get_all_day_stress", "daily"),
    _e("daily_events", "get_all_day_events", "daily"),
    _e("respiration", "get_respiration_data", "daily"),
    _e("spo2", "get_spo2_data", "daily"),
    _e("intensity_minutes", "get_intensity_minutes_data", "daily"),
    _e("hydration", "get_hydration_data", "daily"),
    _e("hrv", "get_hrv_data", "daily", N),
    _e("training_readiness", "get_training_readiness", "daily"),
    _e("morning_training_readiness", "get_morning_training_readiness", "daily", N),
    _e("training_status", "get_training_status", "daily"),
    _e("max_metrics", "get_max_metrics", "daily"),
    _e("fitness_age", "get_fitnessage_data", "daily"),
    _e("daily_weigh_ins", "get_daily_weigh_ins", "daily"),
    _e("lifestyle_logging", "get_lifestyle_logging_data", "daily", E),
    _e("menstrual_day", "get_menstrual_data_for_date", "daily", E),
    _e("nutrition_food_log", "get_nutrition_daily_food_log", "daily", E),
    _e("nutrition_meals", "get_nutrition_daily_meals", "daily", E),
    _e("nutrition_settings", "get_nutrition_daily_settings", "daily", E),
    _e("activities_fordate", "get_activities_fordate", "daily", E),
    _e("daily_steps", "get_daily_steps", "range"),
    _e("body_battery", "get_body_battery", "range"),
    _e("endurance_score", "get_endurance_score", "range"),
    _e("hill_score", "get_hill_score", "range"),
    _e("blood_pressure", "get_blood_pressure", "range"),
    _e("body_composition", "get_body_composition", "range"),
    _e("weigh_ins", "get_weigh_ins", "range"),
    _e("running_tolerance", "get_running_tolerance", "range"),
    _e("weekly_intensity_minutes", "get_weekly_intensity_minutes", "range"),
    _e("menstrual_calendar", "get_menstrual_calendar_data", "range", E),
    _e("activities", "get_activities_by_date", "range"),
    _e("weekly_steps", "get_weekly_steps", "weekly"),
    _e("weekly_stress", "get_weekly_stress", "weekly"),
    _e("race_predictions", "get_race_predictions", "static"),
    _e("lactate_threshold", "get_lactate_threshold", "static"),
    _e("cycling_ftp", "get_cycling_ftp", "static", E),
    _e("pregnancy_summary", "get_pregnancy_summary", "static", E),
    _e("devices", "get_devices", "static"),
    _e("device_last_used", "get_device_last_used", "static"),
    _e("primary_device", "get_primary_training_device", "static"),
    _e("device_alarms", "get_device_alarms", "static", E),
    _e("user_settings", "get_user_profile", "static", S),
    _e("userprofile_settings", "get_userprofile_settings", "static", S),
    _e("activity_types", "get_activity_types", "static"),
    _e("personal_records", "get_personal_record", "static"),
    _e("training_plans", "get_training_plans", "static"),
    _e("workouts", "get_workouts", "static"),
    _e("earned_badges", "get_earned_badges", "static", E),
    _e("available_badges", "get_available_badges", "static", E),
    _e("in_progress_badges", "get_in_progress_badges", "static", E),
    _e("goals", "get_goals", "static_goals"),
    _e("gear", "get_gear", "per_profile", E),
    _e("gear_defaults", "get_gear_defaults", "per_profile", E),
    _e("device_settings", "get_device_settings", "per_device"),
    _e("device_solar", "get_device_solar_data", "per_device_range", E),
)
PER_ACTIVITY: tuple[tuple[str, str], ...] = (
    ("activity_summary", "get_activity"),
    ("activity_details", "get_activity_details"),
    ("activity_splits", "get_activity_splits"),
    ("activity_typed_splits", "get_activity_typed_splits"),
    ("activity_split_summaries", "get_activity_split_summaries"),
    ("activity_weather", "get_activity_weather"),
    ("activity_hr_zones", "get_activity_hr_in_timezones"),
    ("activity_power_zones", "get_activity_power_in_timezones"),
    ("activity_exercise_sets", "get_activity_exercise_sets"),
    ("activity_gear", "get_activity_gear"),
)
DOWNLOADS = (("activity_fit", "ORIGINAL"),)
ALT_DOWNLOADS = (("activity_tcx", "TCX"), ("activity_gpx", "GPX"), ("activity_kml", "KML"),
                 ("activity_csv", "CSV"))  # fmt: skip
DOWNLOAD_EXT = {"ORIGINAL": ("zip", "application/zip"), "TCX": ("tcx", "application/xml"),
                "GPX": ("gpx", "application/xml"), "KML": ("kml", "application/xml"),
                "CSV": ("csv", "text/csv")}  # fmt: skip
_DANGEROUS = ("add_", "set_", "create_", "update_", "upload_", "delete_", "remove_", "schedule_",
              "unschedule_", "reset_", "sync_", "archive_", "merge_", "post_", "put_", "patch_",
              "edit_", "save_", "import_", "login", "logout", "resume_login")  # fmt: skip


def fan_out_collections(alt_formats: bool) -> tuple[str, ...]:
    downloads = DOWNLOADS + (ALT_DOWNLOADS if alt_formats else ())
    return tuple(name for name, _m in PER_ACTIVITY) + tuple(name for name, _f in downloads)


def collection_names() -> set[str]:
    """Every name the selection lists accept: catalog entries and per-activity collections."""
    return {e.name for e in CATALOG} | set(fan_out_collections(alt_formats=True))


# @spec GARMIN-CAPTURE-CAT-002
def selected(name: str, values: dict[str, Any]) -> bool:
    """Not excluded, and the selection is empty or names it; per-activity collections also
    need `activities` selected."""
    select = values.get("WEARSTREAM_GARMIN_SELECT") or []
    exclude = values.get("WEARSTREAM_GARMIN_EXCLUDE") or []
    if name in fan_out_collections(alt_formats=True) and not selected("activities", values):
        return False
    return name not in exclude and (not select or name in select)


def methods() -> set[str]:
    return {e.method for e in CATALOG} | {m for _n, m in PER_ACTIVITY} | {"download_activity"}


# @spec GARMIN-CAPTURE-CAT-001
def forbidden() -> list[str]:
    """Catalog methods that look like they mutate or sign in (always empty)."""
    return sorted(m for m in methods() if m.startswith(_DANGEROUS))


def uncovered(garmin_class: type) -> list[str]:
    """Readable library methods neither in the catalog nor reviewed as not captured."""
    from wearstream.garmin.reviewed import NOT_CAPTURED

    readable = {
        name
        for name in dir(garmin_class)
        if name.startswith(("get_", "download_")) and callable(getattr(garmin_class, name, None))
    }
    return sorted(readable - methods() - NOT_CAPTURED)
