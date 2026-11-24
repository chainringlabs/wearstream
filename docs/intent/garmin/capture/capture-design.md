---
parent: garmin
prefix: GARMIN-CAPTURE
---

# Garmin capture

## Context

This component declares the Garmin jobs and captures every selected catalog endpoint through
a signed-in `garminconnect` client (see [authentication](../auth/auth-design.md)). Because
Garmin restates and back-fills days after the fact, every completed day is re-captured daily
across a trailing window, and skip-identical keeps storage flat (see the parent
[Garmin design](../garmin-design.md)).

## Jobs

| Job | Kind | Schedule (UTC) | Window | Time limit | Backfill pause |
|---|---|---|---|---|---|
| `daily` | partitioned | `30 5 * * *` | 14 days, ending the day before the fire date | 15 minutes | 60 seconds |
| `reference` | snapshot | `30 5 * * *` | — | 10 minutes | — |
| `checks` | checks | `7 3-21/6 * * *` | — | 10 minutes | — |

The `daily` job's collections, for backfill's "does this day have data", are every selected
collection the `daily` unit writes (see *The `daily` unit*). The jobs fit the supervisor's queue
guardrail: 14 × 15 minutes for `daily`, plus 10 for `reference`, plus 15 for one backfill unit,
is 235 minutes within the daily interval. A `daily` unit takes 35 to 105 seconds in practice.

**The window.** Most restatements land by the second capture; the rest arrive in batches when a
device syncs after a gap, and observed batches reached a week back. Fourteen days covers a sync
gap of up to two weeks. Each extra day costs one unit per tick, and storage barely grows because
unchanged re-captures are skipped. A longer gap is filled by backfill only for days that have no
capture at all.

**The day and the tick.** A partition key is a calendar date, which the unit sends to Garmin as
the request's date; Garmin interprets it as the account's **local** calendar day. The window ends
the day before the fire date. 05:30 UTC falls after local midnight in Europe and in the Eastern
time zone, but before it in Central (standard time), Mountain and Pacific, where the newest day
is still in progress at its first capture; that capture is partial and the next 13 captures
complete it. An operator in those time zones can move the schedule later (08:30 UTC, say).

**The backfill pause.** Garmin documents no rate limits, and its sign-in rate-limits bursts. A
`daily` unit makes about 50 calls plus the fan-out, paced 2 seconds apart, so it takes about two
minutes; with a minute's pause between backfill units, two years of history takes about a day
and a half. A 429 puts the source into the supervisor's cooldown rather than burning backfill
attempts. Days before the account existed answer with empty payloads, about 40 small captures
each, so an operator should not set the backfill start much earlier than the account's first
device.

## The catalog and selection

The **catalog** is the allowlist of library methods WearStream may call, one entry per
collection, each with a kind and flags. Every call goes through it.

| Kind | What one entry does | Job | `dt` folder |
|---|---|---|---|
| `daily` | One call for the day (`cdate`) | `daily` | The unit's day |
| `range` | One call with the day as both start and end | `daily` | The unit's day |
| `per_device_range` | One call per discovered device, with the day as start and end | `daily` | The unit's day |
| `static` | One call | `reference` | The UTC fetch date |
| `static_goals` | One call per goal status | `reference` | The UTC fetch date |
| `weekly` | One call covering the trailing 4 weeks | `reference` | The UTC fetch date |
| `per_profile` | One call for the user's profile number | `reference` | The UTC fetch date |
| `per_device` | One call per discovered device | `reference` | The UTC fetch date |

| Flag | Meaning |
|---|---|
| `skip_if_empty` | An empty list or object is not captured (the endpoint writes nothing on days without data) |
| `skip_if_none` | The endpoint returns `None` on days without data (documents why it can go days without writing) |
| `screen_secrets` | The payload passes through the capture component's `screen_secrets` before capture |

- **Read-only by construction.** No mutating or sign-in method is ever in the catalog; a test
  enforces it. Every readable library method the catalog doesn't call is listed as reviewed and
  not captured, and a drift test fails when a library release adds one, so a new library method
  is reviewed rather than called.
- **Selection.** The operator's selection and exclusion lists of catalog names (exclusion wins;
  an empty selection means everything not excluded) turn entries off. A deselected entry is never
  called, has no checks, and is not one of the job's collections for backfill. By default
  everything is selected.
- **Empty answers are kept.** An empty 200 from an unflagged entry is captured as a faithful
  "asked, no data" record, so a device that starts producing a stream begins populating without
  any change.
- **Alternate activity formats.** TCX, GPX, KML and CSV exports of each activity are off by
  default and can be turned on.
- **Per-activity collections** (the fan-out endpoints and downloads below) are not catalog
  entries but can be deselected by their collection names in the same lists; deselecting
  `activities` turns off the whole fan-out.

## The `daily` unit

A `daily` unit captures one partition day `D`, in this order:

1. every selected `daily`, `range` and `per_device_range` entry except `activities`, in catalog
   order (devices are discovered with one call first);
2. the day's activity list (`get_activities_by_date(D, D)`, collection `activities`);
3. for each activity in the list: each per-activity endpoint (`activity_summary`,
   `activity_details`, `activity_splits`, `activity_typed_splits`, `activity_split_summaries`,
   `activity_weather`, `activity_hr_zones`, `activity_power_zones`, `activity_exercise_sets`,
   `activity_gear`), then the original FIT file (`activity_fit`, a `.zip`), then any enabled
   alternate formats.

Every capture of the unit goes into `dt=D` of its own collection; no two collections share a
folder. Calls are paced at least 2 seconds apart (configurable).

If device discovery fails, each `per_device_range` entry is a failed request; if the activity
list fails, the whole fan-out is a failed request. Either way the unit continues with what it
can call, and the day goes to `needs_refill`.

## The `reference` unit

A `reference` unit calls every selected `static`, `static_goals`, `weekly`, `per_profile` and
`per_device` entry, in catalog order, capturing into the UTC fetch-date folder (no `dt` passed).
Each run therefore writes into a new folder once a day. A `weekly` entry asks for the 4 weeks
ending on the fetch date. Devices and the profile number are discovered with one call each first;
if a discovery fails, the entries that depend on it are failed requests.

## What is written

- **`None` is never captured**, for any entry: the call answered "nothing here".
- **An empty list or object** is captured unless the entry is flagged `skip_if_empty`.
- **Capture grades.** Activity downloads are captured byte for byte (`capture_mode` `raw`,
  extension from the format). Everything else is the library's parsed JSON, re-serialized
  compactly with the library's key order and `ensure_ascii` off (`capture_mode` `reserialized`).
- **Sidecar request fields.** `request_url` is the library method name, and `request_params` the
  arguments of the call (`cdate`; `start` and `end`; `end` and `weeks`; `status`;
  `user_profile_number`; `device_id`; `activity_id`, plus `format` for downloads), the same form
  existing captures use: dates as `YYYY-MM-DD` strings, `weeks` and the ids as JSON integers as
  Garmin returned them, `status` and `format` as strings. `http_status` is recorded as 200 for every capture, since the library
  only returns successful answers. Screened entries record what was removed in `redacted_fields`.

## Skip-identical per capture path

The writer's skip-identical check is used only where it is sound on two counts. **One request
per folder per run**: in folder scope the check compares with one file, so where several requests
write one folder (one per activity, device or goal status), two different requests returning the
same bytes, often `[]`, must both be kept. **The entry writes on every run**: freshness relies on
each newly completed day getting a first capture, so an entry that can write nothing on a day
(flagged `skip_if_empty` or `skip_if_none`) keeps every capture, letting its re-captures of older
days keep freshness alive.

| Capture path | Skip-identical |
|---|---|
| Single-call `daily` and `range` entries, the activity list, and `static`, `weekly` and `per_profile` entries, when not flagged `skip_if_empty` or `skip_if_none` | On, folder scope |
| Per-activity endpoints and activity downloads | On, request scope |
| `per_device`, `per_device_range` and `static_goals` entries, when not flagged | On, request scope |
| Any entry flagged `skip_if_empty` or `skip_if_none`, whatever its path | Off: keep every capture |

The setting is decided at each capture call from the capture path and the entry's flags, never
from the entry's kind alone (the fan-out builds its calls without a catalog kind). About 71% of
re-captures are byte-identical to the previous capture, and the fan-out collections, dominated by
FIT files, are about 38% of Garmin's bytes, so request scope matters.

## What the unit reports

| Condition | Reported as |
|---|---|
| A call raises (after the library's own retries) | A failed request for that entry; the unit continues with the next |
| Garmin answers 429 (`GarminConnectTooManyRequestsError`) | Rate limited (child exit `6`); the unit makes no further calls |
| A call fails with `GarminConnectAuthenticationError` after sign-in | A sign-in failure (authentication); the unit makes no further calls |
| A needed credential (authentication) | The needed credential; the unit makes no further calls |
| Less than 3 minutes left before the deadline | A failed request for every entry not yet called; no call starts (one call with the library's retries can take about two minutes) |
| A stop signal is pending | Nothing further: no call or pause starts and the unit ends (the child exits `75`) |

## Collection checks

The checks job evaluates these settings (the checks component defines what each check does).
Freshness is 26 hours plus 6 hours grace everywhere. Completeness takes its event dates from the
partition (`event_date_source` `partition`): each `dt=` day whose capture is not empty, so a day
Garmin answered with an empty payload counts as a gap. For the important collections it also
counts the gap from the newest such day to today (`gap_to_now`), because Garmin writes an empty
capture every day: without it, a stream that stopped producing data would look fresh and complete.
A break from wearing the device therefore warns, which is the intended signal.

| Collection | Checks | Completeness gap |
|---|---|---|
| Important: `sleep`, `stress`, `hrv`, `respiration`, `spo2`, `resting_heart_rate`, `heart_rates`, `steps_intraday`, `intensity_minutes`, `training_status`, `training_readiness`, `max_metrics`, `body_battery`, `user_summary` | Freshness, completeness, schema drift, content health | 2 days; 14 if expected-empty |
| Expected-empty, not important | Freshness, content health | — |
| Every other selected catalog collection | Freshness | — |
| Per-activity collections (`activity_*`) | None | — |
| Deselected | None | — |

**Expected-empty** collections are legitimately empty on common hardware: those flagged
`skip_if_empty` or `skip_if_none`, plus a default list of collections that write an empty payload
every day instead of nothing (`training_readiness`, `body_battery_events`, `running_tolerance`,
`goals`, `max_metrics`, `hrv`, `activities`). The default list comes from a content survey of one
real tree, so the operator can replace it.

**Per-activity collections** get no checks: most days have no activity (about 82% in the surveyed
tree), so freshness would fail through every rest stretch, and the activity list's own checks
already show whether the fan-out's source is alive.

**Schedule.** Garmin captures once a day at 05:30 UTC with a 32-hour freshness tolerance, so a
stopped capture becomes detectable around 13:30 UTC the next day; checks at 03:07, 09:07, 15:07
and 21:07 report it at 15:07, at a sixth of the cost of hourly checks. Those three numbers are
coupled: moving the capture time, the grace or the checks offset re-checks the other two.

The checks job also runs the token-store health check (authentication). Completeness gaps are
warnings: a training break produces one, which is the intended signal.

## Decisions & Alternatives

| Decision | Chosen | Alternatives Considered | Rationale |
|---|---|---|---|
| How often a completed day is captured | Every day it is in the 14-day window | Capture once; a 2–3 day window | Garmin restates and back-fills; capture-once freezes each day at its first, often sparse, pull. *Completeness over economy*. |
| Window length | 14 days | 7 days; 7 plus a weekly deep re-capture | With 7, sync batches repeatedly reached the window's edge, so older days in the batch were never re-fetched. Backfill fills longer gaps only for days with no capture. |
| Unit content | One unit per day for every daily entry plus the fan-out | One unit per entry and day | One sign-in per unit; a unit per entry would multiply sign-ins by about 50 and invite Garmin's SSO rate limit. |
| Storage control | Skip-identical per capture path | Keep every capture; shrink the window | Most re-captures are identical; skipping them never loses data. |
| Which captures may be skipped | Single-request folders whose entry writes every run, plus request scope for multi-request folders | Folder scope everywhere | Folder scope would drop identical answers to different requests and starve freshness for entries that skip no-data days. |
| A 429 inside a unit | Stop calling and report the unit rate-limited | Keep calling; report the rest as failed | Further calls deepen the rate limit, and reporting failures would burn backfill attempts; the supervisor's cooldown holds the source instead. |
| Tick time | 05:30 UTC | Later in the morning | After local midnight across the Americas and Europe, and early enough that downstream daily jobs see the day the same morning. |
| Checks cadence | Four times a day | Hourly; once a day | Hourly gains about an hour of detection at six times the cost; once a day can add nearly a day. |
| Per-activity checks | None | Freshness like other collections | Most days have no activity, so freshness would fail through every rest stretch; the activity list's checks already show the source is alive. |
| Deadline margin | No call starts with less than 3 minutes left | 60 seconds, as for Whoop | One Garmin call, with the library's own retries, can take about two minutes. |
| Selection default | Everything selected | Exclude empty-on-most-hardware entries | A device that starts producing a stream begins populating without configuration. |
| Sidecar request fields | Library method name and call arguments | HTTP URLs | The library doesn't expose its URLs; the method and arguments identify and replay the call, and match existing captures. |
| Catalog coverage | The collections captured before WearStream (61 entries plus the fan-out); every other readable method listed as reviewed | Capture every readable method the library offers | Continuity of the existing tree comes first; most uncovered methods are aliases or range variants, and each new collection needs its own flags and checks. |

## Open Questions & Future Decisions

### Resolved
1. ✅ **Freshness for collections the hardware never produces** is settled by the checks design: an
   expected-empty collection with no captures passes freshness, and one that goes stale warns.

### Deferred
1. **Uncovered library endpoints** (issue #26): evaluate the readable methods listed as reviewed
   and not captured, and catalog any that hold data the tree doesn't.
2. **Collections that change on every pull.** `lifestyle_logging` and `device_settings` differ on
   nearly every capture; if the difference is a volatile field, they grow daily. Normalizing would
   have to be weighed against byte-faithful capture.

## References

- grecohome-dagster `docs/intent/garmin/capture/capture-design.md`
- `docs/intent/supervisor/supervisor-design.md`: jobs, guardrails, exit codes
- `docs/intent/capture/capture-design.md`: skip-identical scopes, "has data", `screen_secrets`
