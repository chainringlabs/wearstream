# Checks — EARS specs

Design: [checks-design.md](checks-design.md). Prefix `CHECKS`. Facets: `SET`, `FRESH`,
`COMPLETE`, `SCHEMA`, `CONTENT`, `RUN`, `SUPP`, `REPORT`, `ACCEPT`.
"A checks unit" is a source's `checks` job child; "the trailing folders" are a collection's
`recent_folders` most recent `dt=` folders holding at least one capture (as the capture read
helpers define a capture); "a source's own checks" are the credential-health checks a source adds.

## Settings

- [ ] **CHECKS-SET-001**: When the daemon starts, it shall validate every source's check settings and exit with an error naming the source and collection if completeness is enabled without an `event_date_source`, `event_date_source` is `payload` without an `event_date_field`, a family is not `freshness`, `completeness`, `schema` or `content`, or `recent_folders` is outside 1 to 60; a `reader` that is not set shall default to `json`.
- [ ] **CHECKS-SET-002**: The checks component shall name each collection check `<source>_<collection>_<family>`, with family `freshness`, `completeness`, `schema_drift` or `content_health`.
- [ ] **CHECKS-SET-003**: The checks component shall read the capture tree only through the capture read helpers, and shall never create, modify or delete anything under `/data`.

## Freshness

- [ ] **CHECKS-FRESH-001**: When a freshness check runs, it shall fail at error severity, with an action to check the source's recent `run_failed` and `run_killed` lines or deselect the collection if the devices don't produce it, if the newest capture's fetch time in the trailing folders is older than `cadence_hours` plus `grace_hours`, and pass otherwise, except as CHECKS-FRESH-003 provides.
- [ ] **CHECKS-FRESH-002**: If a collection has no captures at all when its freshness check runs, then the check shall pass when the collection is `expected_empty` and otherwise fail at error severity with the action of CHECKS-FRESH-001.
- [ ] **CHECKS-FRESH-003**: When the freshness check of an `expected_empty` collection finds the newest capture older than `cadence_hours` plus `grace_hours`, it shall report a warning with the hint to deselect the collection if the devices no longer produce it.

## Completeness

- [ ] **CHECKS-COMPLETE-001**: When a completeness check runs, it shall take event dates only from captures whose payloads are not empty, from the `dt=` folder when `event_date_source` is `partition`, and, when it is `payload`, from the `event_date_field` of each record (with `unnest_records` on, each record of the `records` wrapper; otherwise the payload object, or each element of a payload list) or CSV row.
- [ ] **CHECKS-COMPLETE-002**: When a completeness check parses a `payload` event date, it shall take the calendar date of the first 10 characters of an ISO date or datetime without applying any time-zone offset, then try `%m/%d/%Y`, `%m-%d-%Y` and `%Y/%m/%d`, treat a number as epoch seconds giving a UTC date, and ignore values that don't parse.
- [ ] **CHECKS-COMPLETE-003**: When a completeness check runs, it shall warn if two consecutive distinct event dates are more than `cadence_days` apart, reporting the event days, the earliest and latest, the gaps over the tolerance and the biggest gap.
- [ ] **CHECKS-COMPLETE-004**: Where `gap_to_now` is on, a completeness check shall also warn if the newest event date is more than `cadence_days` before the run's UTC date, including when there is only one event date, and, when the trailing folders hold captures but no event date, shall warn that there is no recent event; when the collection has no captures at all, it shall pass.
- [ ] **CHECKS-COMPLETE-005**: Where `gap_to_now` is off, a completeness check that finds fewer than two distinct event dates shall pass.

## Schema drift

- [ ] **CHECKS-SCHEMA-001**: When a schema-drift check runs, it shall take from each trailing folder the newest capture whose payload is readable and not empty, and collect its keys: a JSON object's top-level keys; the union of the top-level keys of a JSON list's object elements; with `unnest_records`, the union of its records' keys; a CSV payload's column names; or a text payload's first-row field count as one token.
- [ ] **CHECKS-SCHEMA-002**: If no trailing folder holds a readable, non-empty payload, then the schema-drift check shall pass with status `no_payload`.
- [ ] **CHECKS-SCHEMA-003**: When the schema-drift check finds no baseline, an empty one, or one whose `version` differs from the current signature rules, it shall record the collected keys and the current `version` as the baseline at `/state/checks/baselines/<source>/<collection>.json`, pass, and log `schema_baseline_set` at INFO.
- [ ] **CHECKS-SCHEMA-004**: If the baseline exists but is unreadable, then the schema-drift check shall fail at error severity with status `baseline_unreadable` and the action to run `wearstream accept-schema <source> <collection>`.
- [ ] **CHECKS-SCHEMA-005**: If the collected keys include a key the baseline lacks, then the schema-drift check shall fail at error severity with status `drift`, naming the new keys, with the action to accept the change with `wearstream accept-schema <source> <collection>` if it is expected.
- [ ] **CHECKS-SCHEMA-006**: If a baseline key appears in no collected payload and no new key appeared, then the schema-drift check shall warn with status `missing`, naming the keys.

## Content health

- [ ] **CHECKS-CONTENT-001**: When a content-health check runs, it shall sample up to 5 captures: the newest, plus captures spread evenly over the rest of the trailing folders' captures in name order.
- [ ] **CHECKS-CONTENT-002**: If a sampled payload's sha256 or byte size disagrees with its sidecar, or a sampled `.json` payload has zero bytes, then the content-health check shall fail at error severity, naming the file, with the action that it won't be re-captured on its own and should be restored from a backup or moved aside with its sidecar on the host; this applies to `expected_empty` collections too.
- [ ] **CHECKS-CONTENT-003**: If a sampled non-JSON payload has zero bytes, then the content-health check shall warn.
- [ ] **CHECKS-CONTENT-004**: If a sampled `.json` payload classifies as `EMPTY_LIST`, `EMPTY_OBJECT`, `EMPTY_WRAPPER`, `NULL`, `ERROR_LIKE` or `UNPARSEABLE`, or a sampled sidecar records an `http_status` outside 200–299, then the content-health check shall warn, except that `EMPTY_LIST`, `EMPTY_OBJECT`, `EMPTY_WRAPPER` and `NULL` pass for an `expected_empty` collection.
- [ ] **CHECKS-CONTENT-005**: If no capture can be sampled, then the content-health check shall pass.
- [ ] **CHECKS-CONTENT-006**: If a sampled payload's extension disagrees with the collection's `reader`, then the content-health check shall warn, naming the setting, with the action to correct the source's check settings.

## Running

- [ ] **CHECKS-RUN-001**: When a checks unit runs, it shall run the source's own checks first and then every collection check in the source's settings, one after another in the unit's process.
- [ ] **CHECKS-RUN-002**: If a check runs longer than 60 seconds, then the checks unit shall abandon it, record the result CHECKS-RUN-003 gives a check that raised, and run the next check.
- [ ] **CHECKS-RUN-005**: A check shall only compute its result; the checks unit shall make any write a check calls for (a baseline) after accepting that check's result, so an abandoned check writes nothing.
- [ ] **CHECKS-RUN-006**: A checks unit shall start no check with less than 90 seconds left before its deadline, and shall log `checks_skipped` at WARNING naming the checks it did not start.
- [ ] **CHECKS-RUN-007**: A source's own check shall return a severity (`pass`, `warning` or `error`), a status, details, an action when the severity is `error`, and optionally `suppressed` with a reason, and shall be reported like a collection check except that its events carry the check's name in place of a collection and family.
- [ ] **CHECKS-RUN-003**: If a check raises, then the checks unit shall record a result at the check's family severity (error for freshness, schema drift and a source's own checks; warning for completeness and content health) with the action to report the problem with the logged error type, and run the next check.
- [ ] **CHECKS-RUN-004**: A checks unit shall save each check's result as soon as it is known, and exit with code 0 whatever the checks found.

## Suppression

- [ ] **CHECKS-SUPP-001**: While `/state/heartbeat.json` was updated within the last 120 seconds and either the source is waiting for a credential or in a rate-limit cooldown (per `/state/sources/<source>.json`), capture is paused (per the heartbeat), or the source's `last_source_success_at` is not later than its `resumed_at`, its freshness checks and the `gap_to_now` term of its completeness checks shall report `suppressed` with the reason instead of failing.
- [ ] **CHECKS-SUPP-002**: If a collection's newest capture is older than three times `cadence_hours` plus `grace_hours`, then its freshness check shall not be suppressed.
- [ ] **CHECKS-SUPP-003**: While CHECKS-SUPP-001 applies, schema-drift checks, content-health checks and completeness gaps between event dates shall be reported as usual.
- [ ] **CHECKS-SUPP-004**: A source's own check may report `suppressed` with a reason under the conditions of CHECKS-SUPP-001, and shall be reported like a suppressed collection check.

## Reporting

- [ ] **CHECKS-REPORT-001**: The checks component shall keep in `/state/checks/<source>.json`, written only under the source's checks lock, per check, the severity (`pass`, `warning` or `error`) and status (the specific finding) of its last unsuppressed result, since when, its key details (drifted or missing keys, corrupted files, gaps), when it was last logged, and whether it is currently suppressed and why.
- [ ] **CHECKS-REPORT-002**: When a check's unsuppressed result has severity `warning` or `error` and the stored result is absent, passing, of a different severity or status, or of the same severity and status with different key details, the checks component shall log `check_failed` at ERROR with an `action`, or `check_warning` at WARNING, with the source, collection, family, status and details.
- [ ] **CHECKS-REPORT-003**: When a check's unsuppressed result has the same failing severity, status and key details as stored and it was last logged 24 hours or more earlier, the checks component shall log the same event again with `since`.
- [ ] **CHECKS-REPORT-004**: When a check's unsuppressed result passes and the stored severity was failing, the checks component shall log `check_recovered` at INFO; when the stored status is absent, it shall log nothing.
- [ ] **CHECKS-REPORT-005**: When a check's result is `suppressed`, the checks component shall leave its stored severity, status, `since` and last-logged time unchanged, set the suppression flag, and log `check_suppressed` at INFO only when the check was not already suppressed; when a suppressed check next reports an unsuppressed result, it shall log `check_unsuppressed` at INFO and compare that result with the stored severity and status.
- [ ] **CHECKS-REPORT-006**: When a checks unit finds a stored result for a collection check that is no longer in the source's settings, it shall remove it and log `check_removed` at INFO.
- [ ] **CHECKS-REPORT-007**: The checks component shall log a check's event before saving its result.
- [ ] **CHECKS-REPORT-008**: When a checks unit finishes, it shall log `checks_finished` at INFO with the counts of checks passed, warned, failed and suppressed.

## Accepting a schema change

- [ ] **CHECKS-ACCEPT-001**: When `wearstream accept-schema <source> <collection>` runs, it shall take `/state/locks/<source>.checks.lock` (waiting up to 11 minutes, exiting with code 5 if it can't), record the collection's current keys and the current `version` as its baseline atomically, reset the check's stored result, log `schema_baseline_set`, and exit with code 0.
- [ ] **CHECKS-ACCEPT-002**: When `wearstream accept-schema <source> --all` runs, it shall apply CHECKS-ACCEPT-001 to every selected collection of the source whose stored schema-drift status is `drift` or `baseline_unreadable`, naming each in its output.
- [ ] **CHECKS-ACCEPT-003**: If `wearstream accept-schema` is asked for a collection that is not selected or has no readable, non-empty payload in its trailing folders, then it shall change nothing and exit with code 2 and a message saying why.
- [ ] **CHECKS-ACCEPT-004**: The checks component shall keep a deselected collection's baseline.
