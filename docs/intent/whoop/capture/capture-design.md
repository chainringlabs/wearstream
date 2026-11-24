---
parent: whoop
prefix: WHOOP-CAPTURE
---

# Whoop capture

## Context

This component declares the Whoop jobs and captures what Whoop returns: four daily range
collections, re-captured hourly across a trailing window because Whoop rescores and deletes
records after the fact, and two current-only snapshots. It obtains its bearer token from the
[token lifecycle](../auth/auth-design.md) and never retries a token failure.

## Jobs

| Job | Kind | Schedule (UTC) | Window | Collections | Time limit | Backfill pause |
|---|---|---|---|---|---|---|
| `range` | partitioned | `17 * * * *` | 8 days, including the fire date | `sleep`, `recovery`, `workout`, `cycle` | 5 minutes | 60 seconds |
| `snapshots` | snapshot | `17 * * * *` | — | `profile`, `body_measurement` | 5 minutes | — |
| `checks` | checks | `37 * * * *` | — | — | 10 minutes | — |

Together the jobs fit the supervisor's queue guardrail: 8 × 5 minutes for `range`, plus 5 for
`snapshots`, plus 5 for one backfill unit, is 50 minutes within the hourly interval. A `range`
unit takes about 12 seconds in practice.

**Why a 60-second backfill pause.** Whoop allows each client 100 requests a minute and 10,000 a
day. The hourly ticks use about 820 requests a day (8 days × 4 collections, plus 2 snapshots,
every hour). A backfill unit makes about 4 requests, so one unit a minute keeps backfill near
5,800 a day and the total under the daily limit, with room for retries. Two years of history
fills in about 12 hours.

**Why minute 17.** The access token lives about an hour, the same as the cadence, so every
tick refreshes the token right at its expiry. At minute 0 that refresh lands in the
internet-wide top-of-hour surge, when Whoop's token endpoint is slowest and most likely to
return a 5xx mid-rotation; in grecohome-dagster every grant loss while capture ran at minute 0
happened at `HH:00`, and none happened in the months after moving to minute 17. Both capture
jobs share the minute, so the one refresh they need is serialized by the token lock and the
second job reuses the refreshed token.

**Why 8 days including today.** Whoop rescores sleep and recovery for days afterwards and
updates the in-progress cycle every hour; a 7-day reconcile window plus the current day catches
both. The window, not the cadence, is what guarantees rescores are eventually captured.

## The `range` unit

A `range` unit captures one partition day `D` for all four collections, in the order `sleep`,
`recovery`, `workout`, `cycle`. If one collection's requests fail, the unit still captures the
others and then reports the failure.

| Collection | Endpoint |
|---|---|
| `sleep` | `/developer/v2/activity/sleep` |
| `recovery` | `/developer/v2/recovery` |
| `workout` | `/developer/v2/activity/workout` |
| `cycle` | `/developer/v2/cycle` |

Each request sends the partition's **UTC day** as Whoop's time filter: `start` is `D`'s 00:00Z
and `end` the next day's 00:00Z, both with millisecond precision (`YYYY-MM-DDTHH:MM:SS.mmmZ`).
For the current day, `end` is in the future and is sent as is. Which records match is Whoop's
filter: a record spanning midnight (an overnight sleep, an in-progress cycle) can come back for
both days and is captured in both partitions. Readers deduplicate by record id.

Every page that holds at least one record is captured to `whoop/<collection>/dt=D/`, with
skip-identical on (folder scope). Its sidecar's `request_url` is the final request URL including its
query string, and its `request_params` the decoded query of that URL as an object of string
values, keeping the last value of a repeated key (`start`, `end`, `limit` as `"25"` and, after the
first page, `nextToken`). That is exactly the form existing captures use, so new sidecars match
them. Neither ever holds a token. Not captured:

- **Pages with zero records**: the end-of-results page, or a window with no records. A
  collection writes nothing for a day without records, which is common for `workout`.
- **Error responses**: only a 2xx body is captured; for an error, only its status is logged.
- **A 2xx body that isn't JSON**: it raises a non-retryable `WhoopAPIError` and is not captured.

How much each collection writes follows from how often Whoop changes it: the in-progress
`cycle` changes hourly, so today's cycle is written about hourly; `sleep` and `recovery` change
only on rescores, so most re-captures are skipped as identical.

**Known limitation.** Skip-identical compares with the newest capture in the folder, so a day
whose records span more than one page (more than 25 records in one collection) is rewritten on
every re-capture. Real Whoop days fit in one page.

## The `snapshots` unit

| Collection | Endpoint |
|---|---|
| `profile` | `/developer/v2/user/profile/basic` |
| `body_measurement` | `/developer/v2/user/measurement/body` |

Both return a single object, so both are always captured, into the **UTC fetch-date** folder
(no `dt` passed, so the writer uses the UTC date at the moment of the capture), with
skip-identical on. Each day's first capture lands in a new folder and is
written; identical captures later that day are skipped. So each snapshot is written about once
a day, and again only if it changes.

The unit fetches **profile first, then body measurement**. If the profile request fails, body
measurement is not attempted.

## Data requests

- **Base URL** `https://api.prod.whoop.com`. **Timeout** 30 seconds per request. One HTTP client
  per unit, reused across its requests.
- **Rate limit.** A sliding-window limiter per unit at 90% of the configured requests per minute
  (default 60, so 54). Every attempt, retries included, counts. Units of the Whoop source run
  one at a time, and the `range` job's backfill pause spaces backfill units out, so
  per-unit limiting keeps the source well under Whoop's published limits.
- **Retry.** Up to 3 attempts on a timeout, a network error, a 429, or a 500/502/503/504.
  - A `Retry-After` header in seconds is honored, capped at 60 seconds; otherwise attempts are
    2 seconds apart.
  - When attempts run out, the request has failed.
  - Any other error status raises `WhoopAPIError` without retry, as does a protocol error that
    isn't a network error.
  - A `WhoopAuthError` is never retried (see the token lifecycle).

## Pagination

Range endpoints are cursor-paginated. Each request asks for **25 records**, Whoop's maximum,
and sends the previous response's `next_token` as `nextToken`. Pagination ends when a response
has no `next_token` or has zero records.

Whoop sometimes returns a `next_token` that never clears. Two guards end the loop so the unit
finishes within its time limit:

1. **Cursor didn't advance.** A response whose `next_token` was already seen in this pagination
   stops it, with a WARNING. That catches a repeating (`A,A`) or cycling (`A,B,A`) token; a seen
   token is never sent again.
2. **Page cap.** Fetching **20 pages** (500 records in one collection for one day, far beyond any
   real day) stops it, with a WARNING. The cap keeps a runaway cursor inside the unit's time
   limit, so the other collections still run.

When a guard stops pagination, every page fetched so far has already been captured and the
collection counts as successful: a stuck cursor is Whoop's fault, and the next tick re-captures
the window.

## Staying inside the time limit

The unit knows its deadline (its start plus the job's time limit). It starts no request
attempt, data or token, and no wait (a retry pause or a `Retry-After`), that could still be in
progress when less than 60 seconds remain. The collection it was working on and every collection
not yet captured are reported as failed requests, so the day stays in `needs_refill`, and the
unit ends cleanly rather than being killed. The 60 seconds leave room for one credential exchange
to finish.

**A pending stop signal.** Once the unit has received `SIGTERM` or `SIGINT` (held, if it arrived
during a credential exchange), it starts no further request or wait and ends; the supervisor's
child contract then exits `75`. A stop is never reported as a failed request or a needed
credential, and never makes the unit move on to the next collection.

## What the unit reports

| Condition | Reported as |
|---|---|
| A data request fails after its retries, or raises `WhoopAPIError` | A failed request (child exit `1` unless something outranks it) |
| A token failure that needs a person, or a data request answered with 403 (see the token lifecycle) | A needed credential (child exit `4`); the unit makes no further requests |
| A data request answered with 401 | A failed request; the token is marked expired so a later unit refreshes |
| Less than 60 seconds left before the deadline | A failed request for each collection not yet captured |
| A token failure that doesn't need a person | A failed request; the unit makes no further requests, since every later request needs the same token |
| A pagination guard fired | Not a failure; a WARNING |

## Collection checks

The checks job evaluates these settings for each collection (the checks component defines what
each check does). Each check looks at the 14 most recent `dt=` folders that have data.

| Collection | Freshness | Completeness | Schema drift | Content health |
|---|---|---|---|---|
| `sleep`, `recovery`, `cycle` | Newest capture within 26 h + 6 h grace | Event-date gap over 2 days (event date: `start` for sleep and cycle, `created_at` for recovery) | On | On |
| `workout` | **Off** | Event-date gap over **14 days** (event date `start`), including the gap from the newest workout to now | On | **Off** |
| `profile`, `body_measurement` | Newest capture within 26 h + 6 h grace | **Off** (no event timeline) | On | On |

The checks job also runs the token-health check (token lifecycle). Completeness gaps are
warnings, never errors: a training break produces a workout warning, which is the intended
signal.

- **Workouts** are written only on workout days, so freshness would measure "time since the last
  workout", not whether capture is alive, and would fail on every rest day. The daily
  collections' freshness already shows whether Whoop capture is alive. Content health is off for
  the same reason.
- **Snapshots** are written about daily, so freshness detects a stalled `snapshots` job.

## Decisions & Alternatives

| Decision | Chosen | Alternatives Considered | Rationale |
|---|---|---|---|
| Schedule minute | 17 for both capture jobs | Minute 0; staggering the two jobs | Grant losses clustered at `HH:00` and stopped after the move. Staggering doesn't reduce token-endpoint load, because the lock and re-read collapse both jobs into one refresh. |
| Cadence | Hourly | Every 2–3 hours | Hourly keeps intraday data current; a slower cadence is only worth it if grant losses return at minute 17. |
| Unit content | All four collections for one day | One unit per collection and day | The four share a token and a day; one unit per day keeps a tick at 8 units and one refresh, matching the supervisor's day grain. |
| Collection failure inside a unit | Capture the other collections, then report | Stop the unit at the first failure | One collection's outage shouldn't cost the others' data. |
| Range request window | The partition's UTC day | Local days; a rolling window | Partitions are UTC fetch slices (HLD); local-day meaning belongs to readers. |
| Empty pages | Not captured | Capture every response | An empty page is the end-of-results marker or an empty window; capturing it adds a file per day per collection with no information. |
| Page cap | 20 pages | 1,000 pages (grecohome-dagster) | At Whoop's rate limit 1,000 pages take about 18 minutes, far past the unit's 5-minute limit; a stuck cursor would get the unit killed every tick and starve the other collections. |
| Backfill pace | One unit a minute | A request budget read from Whoop's rate-limit headers | A fixed pause keeps backfill under Whoop's documented daily limit without shared state; Whoop's limits are published and stable. |
| Runaway cursor | Stop with a WARNING and keep what was fetched | Fail the unit | Failing gains nothing, since the next tick re-captures the window, and stopping keeps the unit inside its time limit. |
| Rate limiting | Per unit, with a backfill pause between backfill units | A limiter shared across processes through `/state` | Units of a source never overlap, so a per-unit limiter plus the pause bounds the source's rate without shared state. |
| Workout freshness and content health | Off; completeness at 14 days | Freshness with a long tolerance | Workouts are written only on workout days; freshness can't tell a stalled source from a rest week. |

## Open Questions & Future Decisions

### Deferred
1. **Cadence relaxation.** Revisit only if grant losses recur at minute 17.
2. **Multi-page skip-identical.** If Whoop days ever exceed one page, request-scope
   skip-identical keyed on the page cursor would stop the rewrites.

## References

- grecohome-dagster `docs/intent/whoop/capture/capture-design.md`
- `docs/intent/supervisor/supervisor-design.md`: jobs, guardrails, exit codes
- `docs/intent/capture/capture-design.md`: skip-identical, "has data"
