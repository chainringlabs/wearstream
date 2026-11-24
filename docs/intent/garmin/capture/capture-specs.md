# Garmin capture — EARS specs

Design: [capture-design.md](capture-design.md). Prefix `GARMIN-CAPTURE`. Facets: `JOB`, `CAT`,
`DAILY`, `REF`, `WRITE`, `DEDUPE`, `REPORT`, `CHECKS`.
"A `daily` unit" captures one partition day `D`; "a `reference` unit" captures the reference
entries; "selected" means turned on by the operator's selection and exclusion lists.

## Jobs

- [ ] **GARMIN-CAPTURE-JOB-001**: The Garmin source shall declare a `partitioned` job `daily` with schedule `30 5 * * *`, a window of 14 days ending the day before the fire date, a time limit of 15 minutes (minimum 5), a backfill pause of 60 seconds (at least 30 when overridden), and as its collections every selected collection a `daily` unit writes.
- [ ] **GARMIN-CAPTURE-JOB-002**: The Garmin source shall declare a `snapshot` job `reference` with schedule `30 5 * * *` and a time limit of 10 minutes (minimum 5).
- [ ] **GARMIN-CAPTURE-JOB-003**: The Garmin source shall declare a `checks` job `checks` with schedule `7 3-21/6 * * *` and a time limit of 10 minutes (minimum 3).

## Catalog and selection

- [ ] **GARMIN-CAPTURE-CAT-001**: The Garmin source shall call library methods only through its catalog, and the catalog shall contain no method that mutates Garmin data or signs in.
- [ ] **GARMIN-CAPTURE-CAT-002**: The Garmin source shall treat a catalog entry or per-activity collection as selected when its name is not in the exclusion list and either the selection list is empty or contains it; deselecting `activities` shall deselect every per-activity collection.
- [ ] **GARMIN-CAPTURE-CAT-003**: The Garmin source shall never call a deselected entry, and shall leave a deselected collection out of the `daily` job's collections and out of the checks.
- [ ] **GARMIN-CAPTURE-CAT-004**: Where alternate activity formats are enabled, a `daily` unit shall also download each activity as TCX, GPX, KML and CSV into `activity_tcx`, `activity_gpx`, `activity_kml` and `activity_csv`.

## The `daily` unit

- [ ] **GARMIN-CAPTURE-DAILY-001**: When a `daily` unit runs for day `D`, it shall call, in catalog order, every selected `daily` entry with `cdate` `D`, every selected `range` entry except `activities` with `start` and `end` `D`, and every selected `per_device_range` entry once per discovered device with `start` and `end` `D`.
- [ ] **GARMIN-CAPTURE-DAILY-002**: After the entries of GARMIN-CAPTURE-DAILY-001, a `daily` unit shall request the activity list for `D` (`get_activities_by_date(D, D)`), and for each listed activity call each selected per-activity endpoint and then download the original FIT file (`activity_fit`).
- [ ] **GARMIN-CAPTURE-DAILY-003**: A `daily` unit shall make every capture with `dt` `D`.
- [ ] **GARMIN-CAPTURE-DAILY-004**: If device discovery fails in a `daily` unit, then the unit shall report each selected `per_device_range` entry as a failed request and continue; if the activity list fails, it shall report the activity list as a failed request, skip the fan-out, and continue.
- [ ] **GARMIN-CAPTURE-DAILY-005**: The Garmin source shall start each library call at least 2 seconds (configurable) after the previous one ended.

## The `reference` unit

- [ ] **GARMIN-CAPTURE-REF-001**: When a `reference` unit runs, it shall call, in catalog order, every selected `static` entry, the `static_goals` entry once per goal status, each `weekly` entry with `end` the UTC fetch date and `weeks` 4, each `per_profile` entry with the discovered profile number, and each `per_device` entry once per discovered device, capturing every result with no `dt`.
- [ ] **GARMIN-CAPTURE-REF-002**: If device or profile-number discovery fails in a `reference` unit, then the unit shall report each entry depending on it as a failed request and continue.

## What is written

- [ ] **GARMIN-CAPTURE-WRITE-001**: If a Garmin call returns `None`, then the Garmin source shall capture nothing for it, whatever the entry's flags.
- [ ] **GARMIN-CAPTURE-WRITE-002**: If a Garmin call for an entry flagged `skip_if_empty` returns an empty list or object, then the Garmin source shall capture nothing for it.
- [ ] **GARMIN-CAPTURE-WRITE-003**: When a Garmin call for an entry not flagged `skip_if_empty` returns an empty list or object, the Garmin source shall capture it.
- [ ] **GARMIN-CAPTURE-WRITE-004**: The Garmin source shall capture activity downloads byte for byte with `capture_mode` `raw`, and every other return as compact JSON in the library's key order with non-ASCII characters kept, with `capture_mode` `reserialized`.
- [ ] **GARMIN-CAPTURE-WRITE-005**: The Garmin source shall set each capture's `request_url` to the library method name and `request_params` to the call's arguments (dates as `YYYY-MM-DD` strings, `weeks` and ids as JSON integers, `status` and `format` as strings), and `http_status` to 200.
- [ ] **GARMIN-CAPTURE-WRITE-006**: Where an entry is flagged `screen_secrets`, the Garmin source shall pass the parsed return through the capture component's `screen_secrets` before serializing it and record the removed paths in `redacted_fields`.

## Skip-identical

- [ ] **GARMIN-CAPTURE-DEDUPE-001**: When capturing a single-call `daily` or `range` entry, the activity list, or a `static`, `weekly` or `per_profile` entry, not flagged `skip_if_empty` or `skip_if_none`, the Garmin source shall capture with skip-identical on in folder scope.
- [ ] **GARMIN-CAPTURE-DEDUPE-002**: When capturing a per-activity endpoint or download, or a `per_device`, `per_device_range` or `static_goals` entry not flagged `skip_if_empty` or `skip_if_none`, the Garmin source shall capture with skip-identical on in request scope.
- [ ] **GARMIN-CAPTURE-DEDUPE-003**: When capturing an entry flagged `skip_if_empty` or `skip_if_none`, the Garmin source shall capture with skip-identical off, whatever its capture path.

## What the unit reports

- [ ] **GARMIN-CAPTURE-REPORT-001**: If a Garmin call raises an error other than `GarminConnectTooManyRequestsError` or `GarminConnectAuthenticationError`, then the unit shall report a failed request for that entry and continue with the next.
- [ ] **GARMIN-CAPTURE-REPORT-002**: A Garmin unit shall start no call with less than 3 minutes left before its deadline (its start plus its job's time limit), and shall report every entry not yet called as a failed request.
- [ ] **GARMIN-CAPTURE-REPORT-003**: While a stop signal is pending, a Garmin unit shall start no call or pause, shall not move on to another entry, and shall end its job.

## Collection checks

- [ ] **GARMIN-CAPTURE-CHECKS-001**: The Garmin source shall configure, for each selected important collection (`sleep`, `stress`, `hrv`, `respiration`, `spo2`, `resting_heart_rate`, `heart_rates`, `steps_intraday`, `intensity_minutes`, `training_status`, `training_readiness`, `max_metrics`, `body_battery`, `user_summary`), a freshness check, a completeness check (event dates from the partition, `gap_to_now` on; gap over 2 days, or 14 days if the collection is expected-empty), a schema-drift check and a content-health check.
- [ ] **GARMIN-CAPTURE-CHECKS-002**: The Garmin source shall configure, for each selected expected-empty collection that is not important, a freshness check and a content-health check.
- [ ] **GARMIN-CAPTURE-CHECKS-003**: The Garmin source shall configure, for every other selected catalog collection, a freshness check.
- [ ] **GARMIN-CAPTURE-CHECKS-004**: The Garmin source shall configure no check for per-activity collections or deselected collections.
- [ ] **GARMIN-CAPTURE-CHECKS-005**: The Garmin source shall configure every freshness check with a tolerance of 26 hours plus 6 hours grace, and every completeness check at warning severity.
- [ ] **GARMIN-CAPTURE-CHECKS-006**: The Garmin source shall treat as expected-empty every collection flagged `skip_if_empty` or `skip_if_none`, plus the operator's expected-empty list, which defaults to `training_readiness`, `body_battery_events`, `running_tolerance`, `goals`, `max_metrics`, `hrv` and `activities`.
- [ ] **GARMIN-CAPTURE-CHECKS-007**: The Garmin `checks` job shall run the collection checks and `garmin_tokenstore_health`.
