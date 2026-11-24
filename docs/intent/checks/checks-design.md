---
parent: high-level-design
prefix: CHECKS
---

# Checks

## Context and Design Philosophy

A unit that fails says so in the log. What a failing unit can't report is a source that has
**quietly stopped producing data**: captures that stop landing, a payload whose shape changed,
bytes that no longer match their sidecar, a timeline with holes. Checks look at the capture tree
itself, on their own schedule, and tell the operator in the log when something needs attention.

Each source declares a `checks` job and a list of **check settings**, one per collection; this
component provides the check families, runs them, and reports their results. A source never
reimplements check logic; it may add its own checks (the credential-health checks), which follow
the same contract.

**Principles:**

1. **Read-only over `/data`.** Checks never create, modify or delete anything in the capture tree.
   Their state (schema baselines and results) lives under `/state/checks/`.
2. **They see what readers see.** Checks read the tree only through the capture component's read
   helpers, so a temporary file, an orphan or a sidecar is never mistaken for a capture.
3. **Severity is deliberate.** A broken source or capture contract is an **error** (freshness,
   a new field in a payload, corrupted bytes). Gaps, empty payloads and missing optional fields
   are often legitimate (a device not worn, hardware without the feature), so they are
   **warnings**. A check that cries wolf gets ignored.
4. **Bounded.** Each check looks at the collection's 14 most recent `dt=` folders that hold a
   capture (configurable per collection, at most 60), so checks stay fast as the tree grows. For a
   sparse collection those folders can span weeks.
5. **Never raise.** An internal error in a check becomes that check's result, at its family's
   severity, with a generic action, and the remaining checks still run.
6. **Say it once.** A problem is logged when it starts, reminded about once a day while it lasts,
   and logged again when it clears, so log-based alerting sees one event per problem rather than
   one per run.

## Check settings

| Field | Meaning |
|---|---|
| `source`, `collection` | The capture-tree path segments |
| `reader` (`json`) | `json`, `csv` or `txt`: the stored payload form |
| `unnest_records` (off) | The JSON payload is a `{"records": [...]}` wrapper; signatures and event dates look at its records rather than the wrapper |
| `event_date_source` | Required when completeness is on: `payload` (with `event_date_field`, a JSON key with dotted paths allowed, or a CSV column) or `partition` (the `dt` folder is the event date) |
| `cadence_hours` (26), `grace_hours` (6) | Freshness tolerance |
| `cadence_days` (2) | Completeness gap tolerance |
| `gap_to_now` (off) | Completeness also counts the gap from the newest event date to the run's UTC date |
| `expected_empty` (off) | The collection is legitimately empty on common hardware |
| `families` | Which families to run: `freshness`, `completeness`, `schema`, `content` |
| `recent_folders` (14) | How many of the most recent folders with a capture each check reads, from 1 to 60 |

The settings are validated when the daemon starts: completeness without an `event_date_source`,
`payload` without an `event_date_field`, an unknown family, or `recent_folders` out of range stops
the daemon with an error naming the source and collection. A `reader` that disagrees with the
payloads actually found is reported by content health when it runs, since the daemon can't know
the tree's extensions at startup. Check names are
`<source>_<collection>_<family>` (`freshness`, `completeness`, `schema_drift`, `content_health`).

## The four families

### Freshness (error)

*Are new captures still landing?* It takes the newest capture's fetch time across the trailing
folders, through the read helpers, and fails when it is older than `cadence_hours + grace_hours`
(action: check the source's recent `run_failed` and `run_killed` lines; if the person's devices
don't produce this collection, deselect it).

- **Never captured.** A collection with no captures at all passes if it is `expected_empty`
  (hardware that never produces it) and otherwise fails with the same action.
- **Expected-empty and stale.** For an `expected_empty` collection, a newest capture older than the
  tolerance is a **warning**, not an error, with the hint to deselect it if the devices no longer
  produce it.

Freshness measures the last **written** capture. With skip-identical on, it is reliable only when
the collection writes a new file regularly; each source decides per collection whether that holds,
and turns freshness off where it doesn't.

### Completeness (warning)

*Are there gaps in the event timeline?* It collects the distinct **event dates** from captures
whose payloads are **not empty** (an empty list, object or wrapper says "asked, no data", which is
exactly the gap to report), across the trailing folders, and warns when two consecutive event dates
are more than `cadence_days` apart. With `gap_to_now` on, the gap from the newest event date to the
run's UTC date counts too, and applies even with a single event date, or none when the folders hold
only empty captures; a collection with no captures at all passes (a person who never logs a
workout, say). Without `gap_to_now`, fewer than two event dates passes ("too few to assess").
With `cadence_days` 2, one missing day passes and two warn. The details report the event days, the
earliest and latest, the gaps over the tolerance and the biggest gap.

| `event_date_source` | Event date |
|---|---|
| `payload` | The value of `event_date_field` in each record (JSON; with `unnest_records` the wrapper's records, otherwise the payload object or each element of a payload list) or row (CSV). Parsing is best-effort: ISO dates and datetimes give the calendar date of their first 10 characters, with no time-zone offset applied; then `%m/%d/%Y`, `%m-%d-%Y` and `%Y/%m/%d`; numbers are epoch seconds, as a UTC date. Values that won't parse are ignored |
| `partition` | The `dt=` folder of each non-empty capture |

### Schema drift (error for additions, warning for removals)

*Has the payload's top-level shape changed?* For each trailing folder, the newest capture whose
payload is readable and non-empty contributes its **keys**:

- a JSON object: its top-level keys; a JSON list: the union of the top-level keys of its object
  elements; with `unnest_records`: the union of the keys of its records;
- CSV: the column names; text: the field count of the first row (as a one-token signature).

The **baseline** at `/state/checks/baselines/<source>/<collection>.json` holds the union of keys
ever accepted for the collection, with a `version` naming the signature rules it was made with.

| Situation | Result |
|---|---|
| No readable, non-empty payload in the window | Pass, `no_payload` |
| No baseline, an empty one, or one with an older `version` | Record the current keys, pass, log `schema_baseline_set` at INFO |
| Baseline unreadable | **Error**, `baseline_unreadable` (action: `wearstream accept-schema <source> <collection>`) |
| A key appears that the baseline lacks | **Error**, `drift`, naming the new keys (action: if expected, accept with `wearstream accept-schema <source> <collection>`; tools that read `/data` may need updating) |
| A baseline key appears in no payload in the window | **Warning**, `missing`, naming the keys (it may be an optional section with no data lately) |
| Otherwise | Pass |

### Content health (warning; error on corruption)

*Are recent payloads carrying real data, and intact?* It samples up to **5** captures: the newest,
plus an even spread over the rest of the trailing folders' captures in name order, so the same
files are sampled while nothing changes. With each it:

- **classifies** the payload by its extension (`.json` is parsed; any other extension is opaque
  data): `DATA`, `EMPTY_LIST`, `EMPTY_OBJECT`, `EMPTY_WRAPPER` (an empty data array with only
  pagination keys beside it), `ERROR_LIKE` (an error key and no data array), `NULL`, `UNPARSEABLE`;
  a sidecar `http_status` outside 2xx is `HTTP_ERROR`. A payload whose extension disagrees with
  the collection's `reader` is a warning naming the setting (action: correct the source's check
  settings);
- **verifies integrity**: the on-disk sha256 and byte size against the sidecar.

| Outcome | Result |
|---|---|
| sha256 or size mismatch, or a zero-byte `.json` payload | **Error**, naming the file (action: the payload on disk no longer matches what was captured; it won't be re-captured on its own, because skip-identical matches its sidecar, so restore the file from a backup or move both files of the capture aside on the host) |
| A zero-byte non-JSON payload | Warning |
| Any empty, error-like, HTTP-error or unparseable sample | Warning; `expected_empty` collections pass on empties |
| No captures to sample | Pass (freshness covers absence) |
| Otherwise | Pass |

**Known blind spot.** A capture whose sidecar is damaged on disk is not a capture by definition,
so no check sees it; the folder simply looks like it has one fewer capture.

## Running the checks

A source's `checks` unit runs the source's own checks first (they are the most urgent), then every
collection check in its settings, one after another in the unit's process. Each check has a soft
limit of 60 seconds; a check that exceeds it is abandoned with the same result as a check that
raised (its family's severity, with the generic action), and the next one runs. A check only
computes its result; anything it writes (a baseline) is written by the unit once the result is
accepted, so an abandoned check writes nothing. The unit starts no check with less than 90 seconds
left before its deadline, and logs the checks it skipped (`checks_skipped`, WARNING, naming them);
they run on the next run. Results are saved after each check, so a unit killed by its time limit
keeps what it had. The unit exits `0` whatever the checks found: a failing check is a finding, not a
failed run.

**A source's own checks** (Whoop's token health, Garmin's token-store health) return the same kind
of result as a collection check: a severity, a status, details, an action for an error, and
optionally `suppressed` with a reason. Their events carry the check's name in place of a collection
and family, and they are never removed as being "no longer in the settings".

### Suppression

While capture can't happen for a source, freshness and completeness's `gap_to_now` term would only
repeat what the supervisor is already saying. So, for a source that is **waiting for a credential,
in a rate-limit cooldown, or whose capture is paused**, and until a source unit of that source
succeeds after it resumes (its `last_source_success_at` is later than its `resumed_at`, both kept by
the supervisor in `/state/sources/<source>.json`), those report `suppressed` with the reason instead
of failing. All of this is read only while `/state/heartbeat.json` is no more than 120 seconds old;
with no running daemon, nothing is suppressed. Gaps inside the window,
schema drift and content health are still reported: they describe what was captured.

Suppression stops when the newest capture is older than three times the freshness tolerance:
freshness then fails anyway, so repeated short cooldowns can't hide a stopped source.

A source's own checks may report `suppressed` with a reason under the same rule. A problem the
waiting can't explain (Garmin's unusable token store, for example) is reported regardless.

## Reporting

Results are kept in `/state/checks/<source>.json`: per check, the **severity** of its last real
result (`pass`, `warning`, `error`) and its **status** (the specific finding: `drift`, `missing`,
`baseline_unreadable`, `stale`, `gaps`, `corrupted`, `empty`, …), since when, its key details (the
drifted or missing keys, the corrupted files, the gaps), when it was last logged, and separately
whether it is currently suppressed and why. Documents under `/state/checks/` are written only under
the source's checks lock, which already serializes everything that writes them. Each run compares a
check's new result with the stored one:

| Change | Logged |
|---|---|
| None stored (first run, or the document was reset) and the result passes | Nothing |
| A failing severity where there was none, a different severity or status, or the same severity and status with different key details | `check_failed` at ERROR (with an `action`) or `check_warning` at WARNING, with the source, collection, family, status and details |
| The same failing severity, status and key details, last logged 24 hours or more ago | The same event again, with `since` |
| A pass after a failing severity | `check_recovered` at INFO |
| Suppression begins or ends | `check_suppressed` or `check_unsuppressed` at INFO |
| A collection check no longer in the source's settings | Its row is removed; `check_removed` at INFO |

Suppressed runs don't change the stored severity and status: they set the suppression flag only, `since` and the
24-hour reminder clock carry across them, and the next real result is compared with the severity and status
from before the suppression. So an error that clears during a wait logs `check_recovered`, and a cooldown
that comes and goes doesn't re-fire every failing check.

The event is logged before the result is saved, so a unit killed between the two at worst repeats
an event on its next run; it never loses one. An unreadable results document is handled like any
`/state` document (logged as `state_document_reset` and treated as absent).

Details carry payload key names and file paths as list values, never as object keys, so the log's
redaction filter (which matches field names) can't mistake a payload key such as `signature` for a
credential. A hand-started checks run prints its events to the operator's terminal, not the
container's log, so it doesn't update a check's last-logged time: the next scheduled run still
reports the change where alert rules can see it.

Every run ends with `checks_finished` at INFO: counts of checks passed, warned, failed and
suppressed. `wearstream status` lists the checks that are currently failing, from the same
document.

## Accepting a schema change: `wearstream accept-schema`

`wearstream accept-schema <source> <collection>` records the collection's current keys as its
baseline, so the next run passes, and marks the check's stored result as accepted, so that run logs
`check_recovered` and closes any alert on the earlier `check_failed`. With `--all` instead of a
collection, it does this for every collection of the source whose stored schema-drift status is
`drift` or `baseline_unreadable`, naming each in its output. It takes the source's checks lock
(`/state/locks/<source>.checks.lock`, waiting up to the source's effective checks time limit plus
2 minutes, longer than a checks unit can hold it), so it never interleaves with a
checks unit, writes atomically, and logs `schema_baseline_set` for each collection. It refuses, with
a message, for a collection that isn't selected or has no readable, non-empty payload to accept. It
is the supported way to accept a change: the image has no shell, and an operator needn't know
`/state`'s layout.

A deselected collection's baseline is kept, so reselecting it compares against what was accepted
before.

## Decisions & Alternatives

| Decision | Chosen | Alternatives Considered | Rationale |
|---|---|---|---|
| Freshness signal | The newest capture's fetch time | The supervisor's last successful run | A unit can succeed while capturing nothing new; captures are what the person keeps. |
| Severities | Freshness, new keys and corruption are errors; gaps, empties and missing keys are warnings | Everything an error | Gaps, empties and absent optional sections are often legitimate. |
| Logging repeats | On change, a daily reminder while failing, and on recovery | Every run | A log alert would otherwise fire every hour for one problem, and new problems would be buried. |
| Suppression | Freshness and `gap_to_now` only, until the first successful unit after resume, never beyond three tolerances | Suppress every check; never suppress | The supervisor already reports waits and cooldowns; gaps already captured, drift and corruption are not explained by them, and a long-dead source must not stay hidden. |
| Status across suppression | Kept beneath a separate suppression flag | Suppressed replaces the status | Replacing it loses the recovery event and re-fires every failure when a cooldown ends. |
| Schema baseline | The union of accepted keys; new keys error, missing keys warn | The exact signature of one payload | Optional sections come and go; an exact signature flags every absence and every return. |
| Lists and records | The union of element or record keys | The first element's keys | The first element of a list isn't representative, and payloads are often lists. |
| Completeness input | Non-empty captures only | Every capture | Garmin writes an empty capture every day, so counting it would make gaps invisible. |
| Content sampling | Deterministic: newest plus an even spread | Random | Random sampling makes a corrupted file's check flap between failed and recovered. |
| Corruption action | Name the host-side remedy and that it won't self-heal | "Check the disk" | Skip-identical matches the sidecar, so the corrupted payload is never replaced on its own. |
| Accepting a schema change | `wearstream accept-schema`, under the checks lock | Delete the baseline file by hand | The image has no shell; the lock keeps a running checks unit from overwriting the acceptance. |
| Unreadable baseline | An error asking to accept the current schema | Re-record it silently | Silent re-recording can hide a drift that happened while the file was damaged. |
| Baseline versions | Re-record silently when the signature rules change | Compare across versions | A rules change would otherwise report every collection as drifted. |
| Check order and limits | Source checks first; 60 seconds per check; results saved per check | Run in settings order; one limit for the unit | One hung check must not hide every check after it, and credential problems matter most. |
| Exit code of a checks unit | `0` whatever the checks found | Non-zero when a check fails | Findings are reported by the checks; a non-zero exit would also count as a failing job. |

## Open Questions & Future Decisions

### Deferred
1. Push notifications and metrics for check results (issues #3 and #4) would read the same
   transitions this component logs.

## References

- grecohome-dagster `docs/intent/core/checks/checks-design.md`: the families this component
  continues
- `docs/intent/capture/capture-design.md`: the read helpers and the definition of a capture
- `docs/intent/supervisor/supervisor-design.md`: `checks` jobs, source states, `/state`
- `docs/intent/whoop/capture/capture-design.md`, `docs/intent/garmin/capture/capture-design.md`:
  each source's check settings
