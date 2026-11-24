---
parent: high-level-design
prefix: SUPERVISOR
---

# Supervisor

## Context and Design Philosophy

The supervisor is the long-running `wearstream daemon` process. It owns time: when each job
runs, which days it covers, what runs next on each source, how long a run may take, and what is
recorded about it. It never calls a vendor API and never writes to `/data` except through the
capture component's `repair`. Every unit of work runs as a child process (`wearstream run …`)
that the supervisor starts, times and, if necessary, stops.

Three rules shape everything below. **A source is worked on by one process at a time**, because
a source's credential (Whoop's single-use refresh token, Garmin's session) must have one user.
**A credential exchange is never cut off**, because losing a rotated token loses the grant.
**`/state` records progress, `/data` records truth**: losing `/state` costs progress and history,
never data, and every decision the supervisor makes from `/state` can be rebuilt from the
capture tree.

## Jobs and units of work

Each source declares its **jobs**; the supervisor knows nothing source-specific beyond these
declarations.

| Field | Meaning |
|---|---|
| `name` | Unique within the source (`range`, `snapshots`, `daily`, `reference`, `checks`) |
| `kind` | `partitioned` (one unit per partition day), `snapshot` (one unit per tick, no day), or `checks` (one unit per tick, makes no API calls, writes nothing to `/data`) |
| `schedule` | A cron expression evaluated in UTC (see *Schedules*) |
| `window_days`, `include_today` | `partitioned` only: how many trailing UTC days each tick covers, and whether the window ends on the fire date or the day before |
| `collections` | `partitioned` only: the collections the job writes, used by backfill to decide whether a day has data |
| `time_limit` | The longest one unit may run |
| `backfill_pause` | `partitioned` only: the pause between consecutive backfill units of the job |

A **unit** is one child process: `(source, job)` for `snapshot` and `checks` jobs, and
`(source, job, day)` for `partitioned` jobs. Units on a source's queue (every kind except
`checks`) are **source units**; `checks` units are **checks units**. Every unit carries a
**trigger**: `schedule`, `catchup`, `retry`, `backfill`, or `manual` for a unit an operator
starts by hand.

The default declarations reproduce the cadences proven in grecohome-dagster and live in each
source's design. For orientation:

| Source | Job | Kind | Schedule (UTC) | Window | Time limit |
|---|---|---|---|---|---|
| Whoop | `range` | partitioned | `17 * * * *` | 8 days, including the fire date | 5 min |
| Whoop | `snapshots` | snapshot | `17 * * * *` | — | 5 min |
| Whoop | `checks` | checks | `37 * * * *` | — | 10 min |
| Garmin | `daily` | partitioned | `30 5 * * *` | 14 days, ending the day before | 15 min |
| Garmin | `reference` | snapshot | `30 5 * * *` | — | 10 min |
| Garmin | `checks` | checks | `7 3-21/6 * * *` | — | 10 min |

## Schedules

A schedule is a five-field cron expression evaluated in UTC. Its **minute field must be one
literal minute from 1 to 59**: never `0`, `*`, a list, a range or a step. Host cron jobs and
other services crowd minute 0, and a vendor's token endpoint is most congested then. A job's
**interval** is the shortest gap between consecutive fire times of its schedule.

An operator may override any job's schedule, window, time limit or backfill pause through
configuration. These guardrails are checked at startup, and a violation stops the daemon with an
error naming the setting: the minute rule; a window of at least 1 day; a time limit from 1
minute to 6 hours; and a worst case that fits the source's queue. A job's worst case is
`window_days × time_limit` for a `partitioned` job and `time_limit` otherwise. Because a
source's jobs share one queue, the sum of the worst cases of all the source's non-checks jobs,
plus the longest time limit among its `partitioned` jobs (one backfill unit in progress), must be
no longer than the shortest interval among those jobs. Each checks job's `time_limit` must be no
longer than its own interval.

The supervisor computes each job's next fire time from the moment it starts, so fire times that
passed while it was down are never fired; catch-up covers downtime. It sleeps in steps of at most
30 seconds, rechecking the wall clock each time. When a fire time has passed it fires the job
**once**, however many fire times were missed, and never fires a fire time at or before the
job's last recorded tick. A **tick** of a `partitioned` job enqueues one unit per day of its
window, newest day first, where the window is computed from the **fire time's** UTC date, not
the time the supervisor woke; a tick of any other job enqueues one unit. A unit keeps its day
even if it runs past midnight.

## Queues

Each enabled source has one FIFO queue for its source units and runs at most one at a time, so
its credential has one user. Checks units go to a separate per-source checks queue, because they
make no API calls and must not wait behind, or be wedged by, a slow vendor. Source queues and
checks queues run in parallel with each other.

- **Overlap.** A tick of a job is **skipped**, not queued, while that job still has a `schedule`
  or `catchup` unit queued or running. Retry and backfill units never block a tick. The skip is
  logged.
- **Order.** Units run in arrival order. Backfill units are enqueued only when the source's
  queue is empty, so a scheduled tick waits at most for one backfill unit to finish.

## Running a unit

### The child

`wearstream run <source> <job> [--day D]` is the child. When the supervisor starts it, it passes
the run id and trigger and a pipe for the result. In order, the child:

1. takes its lock with a non-blocking exclusive `flock` on `/state/locks/<source>.lock` (source
   units) or `/state/locks/<source>.checks.lock` (checks units), and exits `5` if it is held. The
   lock is held until the process exits; the kernel releases it however the process dies.
2. runs the job, collecting the capture outcomes of every capture it makes.
3. writes one JSON result to the pipe: counts of captures written, skipped as identical, refused
   as non-2xx and failed, a short error summary, for an `environment` failure its errno and the
   folder it happened in, and, when the source reported a needed credential, that fact and the
   source's credential fingerprint at the moment it found the credential unusable.
4. exits with the code that describes the run, choosing the first that applies: `75` if it
   received a stop signal; `3` if any capture failed with `error_kind` `environment` (the child
   ends the job at the first one, making no further requests); `4` if the source reported that
   a person must provide a credential; `6` if the source reported that the vendor is rate-limiting
   it (the source then makes no further requests, and may report how long the vendor asked it to
   wait); `1` if any request or capture failed; otherwise `0`. A
   payload the capture writer refused as non-2xx is not by itself a failed request; whether a
   response is a failed request is the source's decision. An unexpected exception is logged at
   WARNING and exits `70`.

The child turns `SIGTERM` and `SIGINT` into an orderly exit: in-flight captures remove their
temporary files and the process ends with code `75`. While the source is inside a **credential
exchange** (a token refresh and the write of its result), the child holds the signal until the
exchange finishes. Sources keep every exchange within 40 seconds.

For a `partitioned` unit the child passes the unit's day as `dt` on every capture it makes, so
the unit writes only into that day's folders.

### Time limits and outcomes

Time limits are measured on the monotonic clock. When a unit exceeds its job's `time_limit`, the
supervisor sends `SIGTERM` to the child's **process group**, waits 45 seconds, then sends
`SIGKILL`. 45 seconds outlasts a credential exchange.

The supervisor reads the result pipe while the child runs, keeping at most 64 KiB, and stops
reading when the child exits. The exit code and the supervisor's own actions decide the outcome;
the result supplies counts, and a missing or malformed result leaves the counts unknown.

| Outcome | Decided by |
|---|---|
| `killed` | The supervisor signalled the unit for its time limit, whatever its exit code; this includes a shutdown that arrives during the limit's grace period |
| `interrupted` | The supervisor signalled the unit for shutdown, whatever its exit code (such units are classified when found in `inflight.json` at the next start); or exit `75` |
| `success` | Exit `0` |
| `failed` | Exit `1` |
| `environment` | Exit `3` |
| `needs_auth` | Exit `4` |
| `busy` | Exit `5` |
| `rate_limited` | Exit `6` |
| `crashed` | Exit `70`, any other exit code, or death by a signal the supervisor did not send |

### After a unit finishes

- **Repair.** After a source unit ends `killed`, `interrupted` or `crashed`, the supervisor takes
  that source's lock and runs the capture component's `repair` over the days the unit could have
  written: its partition day, or for a `snapshot` unit every UTC date from its start to its end.
  The end of a unit interrupted by a shutdown is the last heartbeat before that shutdown, which
  startup reads before rewriting the heartbeat. Repair never raises, so a folder it cannot clean
  is logged and the supervisor carries on. While a hand-started
  run holds the lock, the source's queue waits and the supervisor retries the lock every 30
  seconds; repair never runs without it. Checks units write nothing to `/data` and are never
  repaired.
- **Retry.** A repaired `schedule` or `catchup` unit is re-queued once at the back of the queue
  with trigger `retry`. An `interrupted` unit with trigger `retry` is re-queued as well, because a
  shutdown is not the unit's fault. A `retry` unit that ends `killed` or `crashed` is not retried
  again. A `backfill` unit is never retried this way; its outcome counts as a backfill attempt.
  Checks units are never retried.
- **Not retried.** A `failed` unit is not retried; the next tick covers its day again while the day
  is in the window, and backfill covers it after that (see *Refill*). A `busy` unit is dropped.
- **Refill.** Every `partitioned` unit's outcome updates its job's `needs_refill` set: a unit that
  does not succeed adds its day; a later `success` for the day removes it.

### Credential needed

When a source unit ends `needs_auth`, or its result reports a needed credential whatever its
outcome (a stop signal can end a unit just after the source found its credential dead), the
source enters `waiting_for_auth`: its queue is
emptied, its ticks and backfill stop, and the supervisor records the **credential
fingerprint** the child reported in its result: the source's fingerprint of the credential it
found unusable, computed without a network call. (If the result carries none, the supervisor
computes it itself.) Recording what the child saw, rather than reading it afterwards, means a
new credential written in the meantime is never mistaken for the dead one. The supervisor logs
an ERROR telling the operator to run `wearstream auth <source>` (for example `docker exec -it
<container> wearstream auth <source>`). Once a minute it computes the fingerprint again; when it
**differs** from the recorded one, the source resumes and catch-up runs. A credential file that
is present but unchanged (a revoked grant) does not resume the source. The ERROR is repeated at
most once every 24 hours while the source waits. Checks units never change a source's state.

### Capture pause

When a source unit ends `environment`, or the supervisor fails to write a `/state` document,
capture is **paused** for every source. The `capture_paused` ERROR names the errno and folder
from the unit's result (or the `/state` path). While paused, no source unit starts, queued
units are held, units already running finish, checks keep running, and the health check reports
unhealthy. The supervisor retries the capture `self_test` on `/data` together with the folder
that failed (and its `/state` write) after 5 minutes, doubling up to 60 minutes; when all pass,
capture resumes and catch-up runs.
If a source unit ends `environment` again within an hour of a resume, the next pause continues
the doubling instead of starting again at 5 minutes.

### Rate limiting

When a source unit ends `rate_limited`, the source enters a **cooldown**: its queue is emptied,
and until the cooldown ends no source unit of it starts, its ticks are skipped (logged as
`tick_skipped`) and backfill waits. The cooldown lasts as long as the vendor asked (the unit's
reported wait), or 60 minutes when it gave none, capped at 24 hours, and is recorded in
`sources/<source>.json` so a restart honours it. The supervisor logs `source_rate_limited` at
WARNING with the end time. When the cooldown ends, catch-up runs for the source. A rate-limited
day goes to the job's `needs_refill` set like any unit that didn't succeed, but it is neither a
failed tick nor a failed backfill attempt, because the vendor's limit is not the day's fault. If
a source is rate-limited three times with no successful source unit in between, the supervisor
logs `source_rate_limited_repeatedly` at ERROR with an action to check the vendor's status and
reduce the source's selection or raise its pacing.

### Failing jobs

A tick **fails** when any of its units (not counting retries) ends `failed`, `killed` or
`crashed`, and **succeeds** when all of them end `success`. Ticks whose units end only with
outcomes that have their own events (`busy`, `interrupted`, `needs_auth`, `environment`,
`rate_limited`) neither
count nor reset. At 3 consecutive failed ticks of a job (configurable) the supervisor logs an
ERROR; the next successful tick logs that the job recovered.

## Catch-up

At startup, and whenever a source resumes from `waiting_for_auth`, a source's rate-limit
cooldown ends, or capture resumes from a pause, every job whose last **completed tick** (all its units finished, whatever their outcomes)
is older than its interval gets one tick immediately, with trigger `catchup`. A job with no
completed tick is caught up, so a fresh install captures as soon as it starts.

## Backfill

Backfill keeps every day from a source's **backfill start** up to the start of each
`partitioned` job's current window captured: it fills history and any gap a long outage leaves.
The backfill start, worked out per job, is the operator's `BACKFILL_FROM` date for that source
when set; otherwise the earliest day on which any of the job's collections **has data** (by the
capture component's definition, so an empty or orphan-only folder does not count); otherwise the
date the source first ran under this `/state`.

A **pass** walks a job's days from the day before its window back to the backfill start, newest
first. A day is **missing** when it is in the job's `needs_refill` set, or when none of the
job's collections has data for it (by the capture component's definition) and it is not
known-empty; in both cases only while it has fewer than three failed backfill attempts. The scan
runs off the scheduling loop, in a worker thread, and stops at the first collection with data
for each day. Each missing day becomes one unit with trigger `backfill`, enqueued only when the
source's queue is empty, with the job's `backfill_pause` between units.

- **When.** A pass starts at startup when none is in progress, after a resume, and once a day
  after the job's first successful scheduled tick of the UTC day. A pass in progress is resumed
  from its saved position after a restart; a start requested while a pass is in progress makes a
  new pass start when it finishes.
- **Known-empty.** A backfill unit that ends `success`, after which none of the job's
  collections has data for its day, records the day as known-empty. A success that still leaves
  the day without data must not leave it missing forever. A known-empty day is fetched once more 30 days later (sources can sync late); if
  it is empty again, it stays known-empty.
- **Attempts.** A backfill unit ending other than `success`, `interrupted`, `busy` or
  `rate_limited` (after any
  repair) is one failed attempt for its day; a shutdown or a lock held by someone else is not the
  day's fault. After three, the day is skipped, reported once at WARNING, and listed by
  `wearstream status`.
- **Moving the start.** An earlier `BACKFILL_FROM` makes the next pass walk further; a later one
  ends the current pass at the new start.

## Hand-started runs

An operator may run `wearstream run <source> <job> [--day D]` at any time; it runs with trigger
`manual`. `--day` is required for a `partitioned` job and refused for any other; a wrong use
exits `2` with a usage message.

In order, a hand-started run:
1. checks the heartbeat: if the daemon is running with capture paused, it exits `3` with a
   message saying why; if no daemon is running, it runs the capture `self_test` itself and exits
   `3` if that fails;
2. checks `sources/<source>.json`: if the source is waiting for a credential, it exits `4` with a
   message saying to run `wearstream auth <source>`, because running would present a credential
   the source already found unusable; if the source is in a rate-limit cooldown, it exits `6`
   with the time the cooldown ends;
3. takes the source lock (exiting `5` if it is held);
4. records itself in `inflight.json`, with its pid and its pid namespace, and in the run log;
5. runs the job under its own time limit: when the job's `time_limit` passes, it stops itself as
   if it had received `SIGTERM`, and ends the process outright 45 seconds later if it is still
   running;
6. records its finish and updates the job's `needs_refill` set, then removes its `inflight.json`
   entry.

A hand-started run is not a tick and counts toward neither catch-up nor failing jobs. Because it
limits itself, it can never hold the source lock longer than its time limit plus the grace.

Once a minute the daemon looks for `manual` entries in `inflight.json` whose source lock it can
acquire: such a run ended without recording its finish (a dropped session, an end it could not
handle). The daemon repairs it under the source lock, records it `interrupted`, and does not
re-run it. On shutdown the daemon also sends `SIGTERM` to every live hand-started run in its own
pid namespace (one started with `docker exec`) and includes it in the shutdown wait, because the
container's stop would otherwise end it without warning, possibly inside a credential exchange.
A run in another container (`docker run --rm`) is not affected by this container's stop.

## State directory

Everything is plain files under `/state`, which must be on a local filesystem (`flock` is not
reliable on network filesystems). JSON documents are written atomically (temporary file, fsync,
rename, fsync the folder). The run log is JSON Lines, appended one line per write.

| Path | Contents |
|---|---|
| `daemon.lock` | Held (`flock`) by the running daemon; a second daemon on the same `/state` refuses to start |
| `heartbeat.json` | Daemon pid, phase (`starting`, `running`, `stopping`), last update, capture pause reason and next retry, each source's status |
| `inflight.json` | Units started and not yet finished and repaired: run id, source, job, day, trigger, start time, `daemon` or `manual` |
| `jobs/<source>.<job>.json` | Last tick, last completed tick, consecutive failed ticks |
| `sources/<source>.json` | `active` or `waiting_for_auth`, any rate-limit cooldown end and the count of consecutive rate limits, since when, recorded credential fingerprint, first-run date, last reminder; read by checks and hand-started runs, where a missing document means `active` |
| `backfill/<source>.<job>.json` | Pass position and pending-restart flag, `needs_refill` days, known-empty days with the date each was recorded, failed-attempt counts |
| `runs/<YYYY-MM>.jsonl` | Run log: a `start` and a `finish` line per unit (trigger, day, outcome, exit code, duration, counts, error summary), and a `repaired` line after repair; the 13 most recent monthly files are kept |
| `locks/` | Source and checks lock files, and `state.lock` |

`state.lock` serializes each read-modify-write of a JSON document between the daemon and
hand-started runs. It is held for one update at a time and waited for at most 10 seconds; an
update that times out is logged and skipped, and the next update retries.

An entry is added to `inflight.json`, and flushed to disk, **before** its child is spawned, so a
crash at any point leaves a record. A missing or unparseable state document is logged at
WARNING and treated as absent, except `inflight.json`: when it is unparseable, the units to
recover are the run log's `start` lines, in the current and previous month's files, that have
no `finish`. Every other document is rebuildable from `/data` or simply restarts.

## Lifecycle

**Startup**, in order:
1. Take `daemon.lock`; if another daemon holds it, exit with an error.
2. Read the previous `heartbeat.json` (its last update marks when the previous daemon stopped),
   then write it with phase `starting`.
3. Load and validate configuration; on a violation, exit with an error naming the setting.
4. Confirm `/state` is writable; if not, exit.
5. Run the capture `self_test` on `/data`; on failure, start with capture paused.
6. Set phase `running` and enter the schedule loop. For each source with `daemon` entries in
   `inflight.json` (or, if it is unparseable, in the run log), the loop first recovers them:
   records each as `interrupted`, repairs source units, re-queues them as described above and
   clears the entries. Recovery holds only that source's queue; `manual` entries are left to the
   once-a-minute check.
7. Run catch-up and start backfill passes; each source's catch-up and backfill units queue
   behind its recovery.

**Shutdown**, on `SIGTERM` or `SIGINT`:
1. Set phase `stopping` and stop starting units.
2. Send `SIGTERM` to every child's process group and to every live hand-started run in this
   pid namespace, and wait up to 45 seconds.
3. `SIGKILL` whatever remains of the daemon's children, and exit 0.

Daemon units cut off here stay in `inflight.json` and are recovered at the next start, whatever
exit code they managed before the daemon stopped.

**Heartbeat.** The scheduling loop itself rewrites `heartbeat.json` on every pass (at least every
30 seconds), so a stuck loop shows as a stale heartbeat. `wearstream healthcheck` reads only
`/state` and exits non-zero when the heartbeat is more than 120 seconds old or records a capture
pause; otherwise zero. A source waiting for a credential is healthy: the container works, and
the log says what to do.

## Logging

Every supervisor line carries `source` and `job` where they apply, and run lines carry `run_id`,
`trigger` and `day`. Every line at ERROR from any WearStream process (the daemon, a child, or a
hand-started command) carries an `action` field in plain language; a child's own unexpected
failure is logged at WARNING and escalated by the daemon's `run_crashed` and `job_failing`.

| Event | Level | When |
|---|---|---|
| `daemon_started` / `daemon_stopping` | INFO | Startup complete / shutdown begun |
| `run_started` | INFO | A unit started |
| `run_finished` | INFO | A unit succeeded; adds duration and capture counts |
| `run_failed` | WARNING | A unit ended `failed`; adds counts and the error summary |
| `run_crashed` | WARNING | A unit ended `crashed`; adds the exit code or signal |
| `run_killed` | ERROR | A unit exceeded its time limit; action: check network and vendor status, or raise the job's time limit if runs are legitimately slow |
| `run_busy` | WARNING | The source lock was held; usually a hand-started run |
| `run_repaired` | INFO | Repair ran after an interrupted, killed or crashed unit |
| `tick_skipped` | INFO | A tick was skipped because the job still had scheduled work queued or running |
| `catchup_scheduled` | INFO | A job was caught up |
| `backfill_pass_started` / `backfill_pass_finished` | INFO | Days missing, filled, known-empty and given up |
| `backfill_day_abandoned` | WARNING | A day failed three backfill attempts |
| `job_failing` | ERROR | A job reached the consecutive-failure threshold; action: read that job's recent `run_failed` lines |
| `job_recovered` | INFO | A failing job succeeded |
| `source_waiting_for_auth` | ERROR | A source needs a credential; action: run `wearstream auth <source>` |
| `source_resumed` | INFO | A waiting source has a new credential |
| `source_rate_limited` | WARNING | A source entered a rate-limit cooldown; adds the end time |
| `source_rate_limited_repeatedly` | ERROR | Three cooldowns with no success between; action: check the vendor's status, reduce the source's selection or raise its pacing |
| `capture_paused` | ERROR | Capture paused; adds the errno and folder; action names the fix (free space, permissions on that folder, hard-link support, `/state` writable) |
| `capture_resumed` | INFO | The checks passed again |
| `inflight_recovered` | WARNING | A unit interrupted by a stop or a dropped hand-started run was recovered |
| `state_document_reset` | WARNING | A state document was missing or unreadable and was treated as absent |
| `state_update_skipped` | WARNING | A state update timed out waiting for `state.lock` |

## Obligations on other components

- **Sources** declare their jobs; report a needed credential so the child exits `4`; report
  that the vendor is rate-limiting them, with the wait it asked for when known, so the child
  exits `6`; decide
  which responses are failed requests; provide a credential fingerprint without a network call,
  taken at the moment the credential is found unusable and on request;
  mark each credential exchange so the child defers stop signals during it; keep every exchange
  within 40 seconds; and make every capture of a `partitioned` unit with that unit's day as
  `dt`.
- **Delivery** runs `tini` as the image entrypoint, so the daemon is not PID 1 and orphaned
  processes are reaped; documents a container stop timeout of at least 60 seconds and a health
  check start period of 120 seconds; and documents that `/state` must be a local filesystem.

## Decisions & Alternatives

| Decision | Chosen | Alternatives Considered | Rationale |
|---|---|---|---|
| Unit grain | One child per source, job and partition day | One child per tick covering the whole window | Matches the capture tree's grain and grecohome's proven run shape; a slow or failing day doesn't hold the rest of the window hostage, and progress, retries and limits are per day. |
| Mutual exclusion | Per-source `flock` taken by the child, plus the supervisor's per-source queue | Supervisor-only queue; PID lock files | The queue orders scheduled work; the kernel lock also excludes hand-started runs and is released whatever way a process dies. PID files go stale. |
| Checks isolation | Separate checks queue and lock per source; checks never repaired, retried or able to change source state | Checks share the source queue | Checks make no API calls and write nothing to `/data`; queuing them behind a slow vendor delays the very alert that would explain it. |
| Outcome authority | The exit code and the supervisor's own signals decide; the result pipe supplies counts | Trust the result object | A child that handles `SIGTERM` may exit cleanly after being cut off; only the supervisor knows it signalled it. A result can be missing when a child dies. |
| Stopped-child exit code | A dedicated code (`75`) checked before any other | Exit through the normal path with `0` or `1` | A child stopped before its first failure would otherwise report success, marking a half-fetched day known-empty and dropping it from refill. |
| Hand-started run limits | The run enforces its job's time limit on itself; the daemon signals live ones at shutdown | No limit; rely on the operator | A hung hand-started run would hold the source lock indefinitely with no ERROR, and a container stop would end it mid credential exchange. |
| Stop grace | 45 seconds; credential exchanges defer stop signals; 60-second container stop timeout | Docker's 10-second default; a short grace | Tenet: protect the credential. A kill between Whoop rotating a refresh token and the new one being saved loses the grant. |
| PID 1 | `tini` in the image | Require `--init` from every operator; a reaper in the daemon | Bundling it removes a step every deployment would otherwise have to remember. |
| Overlapping ticks | Skip the tick while the job has scheduled or catch-up work in flight | Queue it; let any unit of the source block it | Trailing windows make the next tick cover the same days. Per-job scope keeps jobs sharing a minute (Whoop `range` and `snapshots`) from blocking each other, and retries and backfill never cost a scheduled tick. |
| Missed ticks | Fire once; compute fires from startup; catch up on last completed tick | Fire every missed tick; never catch up | One run covers the whole window, so repeats add API load and nothing else. |
| Retry policy | Retry `killed`, `crashed` and `interrupted` scheduled units once; leave `failed` to the next tick and backfill | Retry everything with backoff | A failure inside a unit has already used the source's own request retries; a killed or crashed unit may have half-written its day, so it earns one immediate retry. |
| Refilling partly captured days | A `needs_refill` set per job, used by backfill | Treat only days with no data as missing | A day with some collections captured and others failed has data, so "no data" alone would never refill it once it leaves the window. |
| Backfill trigger | Gap-filling passes against `/data` at startup, on resume and daily | A one-time historical backfill with a cursor | A one-time cursor never notices the gap a long outage leaves between old and new windows. Re-deriving missing days from `/data` is cheap and keeps `/state` a rebuildable cache. |
| Backfill default start | `BACKFILL_FROM`, else the earliest day in `/data`, else the first-run date | Always the first-run date; a fixed date | Adopting an existing tree, or losing `/state`, must still fill the gap since the last capture. Pulling history before the tree began stays a deliberate operator choice. |
| Empty days | Known-empty after an all-zero success, re-checked once after 30 days | Refetch every pass; permanent on first sight | Refetching wastes API calls daily; a single re-check catches devices that sync late. |
| Credential resume | Resume when the credential fingerprint changes | Resume when a credential is present | A revoked grant leaves its token file in place; resuming on presence would flap every tick. |
| Data-directory or state failure | Pause capture, report unhealthy, retry with backoff | Exit and let the container restart | A restart loop repeats startup work and noise; pausing keeps checks and the log running and resumes on its own. |
| In-flight recovery | `inflight.json` written before spawn; run log as fallback | Scan the run log only | A small set read once is exact; the run log covers a corrupted set. |
| Scheduling library | Cron expressions with WearStream's own guardrails | A job-scheduling framework (APScheduler) | The supervisor needs next-fire-time arithmetic, not a framework with its own threads, stores and misfire semantics. |

## Open Questions & Future Decisions

### Deferred
1. Choice of cron-expression library: decide at implementation, pinned, needing only next and
   previous fire times in UTC.
2. Push notifications for ERROR events (issue #4) and metrics (issue #3) would read the same
   events this component logs.

## References

- `docs/high-level-design.md`: "A supervisor with one child process per run", "The schedule is
  part of the product", "Backfill fills missing days and yields to the schedule", "State is
  plain files"
- `docs/intent/capture/capture-design.md`: "Obligations on the supervisor", `repair`, `self_test`,
  `error_kind`
- grecohome-dagster `docs/intent/whoop/capture/capture-design.md`,
  `docs/intent/garmin/capture/capture-design.md`: the schedules this component reproduces
