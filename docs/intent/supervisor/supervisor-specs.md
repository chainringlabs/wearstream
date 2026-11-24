# Supervisor — EARS specs

Design: [supervisor-design.md](supervisor-design.md). Prefix `SUPERVISOR`. Facets: `JOB`,
`SCHED`, `QUEUE`, `CHILD`, `LIMIT`, `OUTCOME`, `AFTER`, `AUTH`, `RATE`, `PAUSE`, `FAIL`, `CATCHUP`,
`BACKFILL`, `MANUAL`, `STATE`, `LIFE`, `HEALTH`, `LOG`.
"The supervisor" is the `wearstream daemon` process; "the child" is a `wearstream run` process.
A **source unit** is a unit of any job kind except `checks`; a **checks unit** is a unit of a
`checks` job. A **tick** is one firing of a job's schedule.

## Job declarations and guardrails

- [x] **SUPERVISOR-JOB-001**: When the supervisor starts, it shall load each enabled source's job declarations (name, kind, schedule, window days, include-today, collections, time limit, backfill pause) and apply the operator's configured overrides to them.
- [x] **SUPERVISOR-JOB-002**: If, after overrides, a job's schedule does not have one literal minute from 1 to 59 in its minute field, then configuration validation shall report the setting as invalid (OPERATOR-VAL-002).
- [x] **SUPERVISOR-JOB-003**: If, after overrides, a `partitioned` job's window is less than 1 day, any job's time limit is below the job's declared `min_time_limit` or above 6 hours, any job's longest gap between consecutive fire times is longer than 24 hours, or a source's queue cannot fit its work, then configuration validation shall report the setting as invalid (OPERATOR-VAL-002). A source's queue fits when the sum of the worst cases of the source's non-checks jobs (`window_days × time_limit` for a `partitioned` job, `time_limit` otherwise), plus the longest time limit among its `partitioned` jobs, is no longer than the shortest interval (smallest gap between consecutive fire times) among those jobs, and each checks job's time limit is no longer than its own interval.

## Schedules

- [x] **SUPERVISOR-SCHED-001**: The supervisor shall evaluate every job schedule in UTC.
- [x] **SUPERVISOR-SCHED-002**: When the supervisor starts, it shall compute each job's next fire time from the startup time, so that fire times that passed while it was not running are never fired.
- [x] **SUPERVISOR-SCHED-003**: While waiting for the next fire time, the supervisor shall sleep in steps of at most 30 seconds, re-reading the wall clock after each step.
- [x] **SUPERVISOR-SCHED-004**: When one or more of a job's fire times have passed, the supervisor shall fire that job once, and shall never fire a fire time at or before the job's last recorded tick.
- [x] **SUPERVISOR-SCHED-005**: When a tick of a `partitioned` job fires, the supervisor shall enqueue one unit per day of the job's window, newest day first, computing the window from the UTC date of the tick's fire time: the `window_days` days ending on that date where `include_today` is set, otherwise the `window_days` days ending the day before.
- [x] **SUPERVISOR-SCHED-006**: When a tick of a `snapshot` or `checks` job fires, the supervisor shall enqueue one unit.

## Queues

- [x] **SUPERVISOR-QUEUE-001**: The supervisor shall keep one first-in-first-out queue of source units per enabled source and run at most one unit from it at a time.
- [x] **SUPERVISOR-QUEUE-002**: The supervisor shall keep a separate first-in-first-out queue of checks units per enabled source, run at most one unit from it at a time, and run checks queues and source queues independently of each other.
- [x] **SUPERVISOR-QUEUE-003**: While a job has a unit with trigger `schedule` or `catchup` queued or running, the supervisor shall skip any new tick of that job and log `tick_skipped`.
- [x] **SUPERVISOR-QUEUE-004**: The supervisor shall not skip a tick because of queued or running units with trigger `retry` or `backfill`.
- [x] **SUPERVISOR-QUEUE-005**: The supervisor shall enqueue a backfill unit only while that source's queue is empty and no source unit of that source is running.

## The child

- [x] **SUPERVISOR-CHILD-001**: When the supervisor starts a unit, it shall start `wearstream run` as a child in a new process group, passing the source, job, day where the job is `partitioned`, run id, trigger, and the write end of a dedicated result pipe.
- [x] **SUPERVISOR-CHILD-002**: When the child starts, it shall take a non-blocking exclusive `flock` on `/state/locks/<source>.lock` for a source unit or `/state/locks/<source>.checks.lock` for a checks unit, hold it until it exits, and exit with code 5 if the lock is held by another process.
- [x] **SUPERVISOR-CHILD-003**: Before exiting after running its job, the child shall write one JSON result to the result pipe holding the counts of captures written, skipped as identical, refused as non-2xx and failed, a short error summary, for a capture that failed with `error_kind` `environment` its errno and the folder it happened in, when the source reported a needed credential, that fact and the source's credential fingerprint taken when the source found the credential unusable, and, when the source reported a rate limit, the wait the vendor asked for as `retry_after_seconds` (whole seconds) if it gave one.
- [x] **SUPERVISOR-CHILD-004**: When the child receives a capture result with `error_kind` `environment`, it shall end its job without making further requests or captures.
- [x] **SUPERVISOR-CHILD-005**: When the child finishes its job, it shall exit with the first code that applies: 75 if it received a stop signal (`SIGTERM` or `SIGINT`, or its own time limit for a hand-started run); 3 if any capture failed with `error_kind` `environment`; 4 if the source reported that a person must provide a credential; 6 if the source reported that the vendor is rate-limiting it; 1 if the source reported a failed request or any capture ended `failed`; otherwise 0. A capture refused as non-2xx shall not by itself make the exit code 1.
- [x] **SUPERVISOR-CHILD-006**: If an unexpected exception reaches the child's top level, then the child shall log it at WARNING and exit with code 70.
- [x] **SUPERVISOR-CHILD-007**: When the child receives `SIGTERM` or `SIGINT` outside a credential exchange, it shall end its run through its normal exit path, so that in-flight captures remove their temporary files, and exit with code 75.
- [x] **SUPERVISOR-CHILD-008**: While the child's source is inside a credential exchange (a token refresh and the write of its result), the child shall hold a received `SIGTERM` or `SIGINT` and act on it when the exchange finishes.
- [x] **SUPERVISOR-CHILD-009**: While running a unit of a `partitioned` job, the child shall make every capture with the unit's day as `dt`.

## Time limits

- [x] **SUPERVISOR-LIMIT-001**: The supervisor shall measure each unit's running time on the monotonic clock.
- [x] **SUPERVISOR-LIMIT-002**: When a unit has run longer than its job's time limit, the supervisor shall send `SIGTERM` to the child's process group, and shall send `SIGKILL` to that process group if the child has not exited 45 seconds later.

## Outcomes

- [x] **SUPERVISOR-OUTCOME-001**: While a child runs, the supervisor shall read its result pipe concurrently, keep at most 64 KiB, and stop reading when the child exits.
- [x] **SUPERVISOR-OUTCOME-002**: When a unit ends, the supervisor shall classify it as `killed` if the supervisor signalled it for its time limit (including when a shutdown arrives during that limit's grace period); as `interrupted` if the supervisor signalled it for shutdown; and otherwise by exit code: 0 `success`, 1 `failed`, 3 `environment`, 4 `needs_auth`, 5 `busy`, 6 `rate_limited`, 75 `interrupted`, and any other exit code, or death by a signal the supervisor did not send, `crashed` (an exit 2, a usage error the daemon should never cause, is logged with reason `usage`).
- [x] **SUPERVISOR-OUTCOME-003**: If a finished unit's result is missing or is not a valid JSON result, then the supervisor shall record its counts as unknown and classify its outcome by SUPERVISOR-OUTCOME-002 unchanged.

## After a unit

- [x] **SUPERVISOR-AFTER-001**: When a source unit ends `killed`, `interrupted` or `crashed`, the supervisor shall take that source's lock and run the capture `repair` for that source over the unit's partition day, or for a `snapshot` unit every UTC date from its start to its end, before any further source unit of that source starts. For a unit interrupted by a shutdown, its end is the update time of the last heartbeat written before that shutdown.
- [x] **SUPERVISOR-AFTER-002**: While the source lock needed for a repair is held by another process, the supervisor shall hold that source's queue and retry the lock every 30 seconds, and shall not run the repair without the lock.
- [x] **SUPERVISOR-AFTER-003**: The supervisor shall never run `repair` for a checks unit, never retry a checks unit, and never change a source's state because of a checks unit's outcome.
- [x] **SUPERVISOR-AFTER-004**: When a source unit with trigger `schedule` or `catchup` ends `killed` or `crashed`, or a source unit with trigger `schedule`, `catchup` or `retry` ends `interrupted`, the supervisor shall, after the repair, enqueue the same unit once at the back of the source's queue with trigger `retry`.
- [x] **SUPERVISOR-AFTER-005**: The supervisor shall not retry a unit with trigger `retry` that ends `killed` or `crashed`, a unit with trigger `backfill` or `manual`, or a unit that ends `failed` or `busy`.
- [x] **SUPERVISOR-AFTER-006**: When a `partitioned` source unit ends other than `success`, the supervisor shall add its day to the job's `needs_refill` set; when a `partitioned` source unit ends `success`, the supervisor shall remove its day from that set.

## Credentials

- [x] **SUPERVISOR-AUTH-001**: When a source unit ends `needs_auth`, or a source unit's result reports a needed credential whatever the unit's outcome, the supervisor shall put the source in `waiting_for_auth`, empty its queue, stop its ticks and backfill, record the credential fingerprint from the unit's result (or, if the result has none, compute the source's fingerprint itself), and log `source_waiting_for_auth` at ERROR with an action telling the operator to run `wearstream auth <source>`.
- [x] **SUPERVISOR-AUTH-002**: While a source is in `waiting_for_auth`, the supervisor shall, once a minute, obtain the source's credential fingerprint without a network call, and resume the source only when it differs from the recorded fingerprint.
- [x] **SUPERVISOR-AUTH-003**: While a source is in `waiting_for_auth`, the supervisor shall repeat `source_waiting_for_auth` at most once every 24 hours.
- [x] **SUPERVISOR-AUTH-004**: When a source leaves `waiting_for_auth`, the supervisor shall log `source_resumed` and run catch-up for that source's jobs.

## Rate limiting

- [x] **SUPERVISOR-RATE-001**: When a source unit ends `rate_limited`, the supervisor shall empty that source's queue and record in `/state/sources/<source>.json` a cooldown ending after the unit result's `retry_after_seconds`, or 60 minutes when it has none, capped at 24 hours, and log `source_rate_limited` at WARNING with the end time.
- [x] **SUPERVISOR-RATE-002**: While a source's cooldown has not ended, the supervisor shall start no source unit of that source, skip its ticks with `tick_skipped`, and enqueue no backfill unit for it.
- [x] **SUPERVISOR-RATE-003**: When a source unit ends `rate_limited` for the third time with no source unit of that source ending `success` in between, the supervisor shall flag the source as repeatedly rate-limited in `/state/sources/<source>.json` until a source unit of it succeeds, and log `source_rate_limited_repeatedly` at ERROR with an action to check the vendor's status and reduce the source's selection or raise its pacing.
- [x] **SUPERVISOR-RATE-004**: When a source's cooldown ends, the supervisor shall run catch-up for that source's jobs.

## Capture pause

- [x] **SUPERVISOR-PAUSE-001**: When a source unit ends `environment`, or the supervisor fails to write a `/state` document, the supervisor shall pause capture for every source and log `capture_paused` at ERROR with the errno and folder from the unit's result (or the `/state` path that failed) and an action naming the fix.
- [x] **SUPERVISOR-PAUSE-002**: While capture is paused, the supervisor shall start no source unit, keep queued source units queued, let running units finish, and keep running checks units.
- [x] **SUPERVISOR-PAUSE-003**: While capture is paused, the supervisor shall re-run the capture `self_test` on `/data` together with the folder that failed, and a write to `/state`, after a delay that starts at 5 minutes and doubles after each failed attempt up to 60 minutes, and resume capture when both succeed.
- [x] **SUPERVISOR-PAUSE-004**: If a source unit ends `environment` within one hour after capture resumed, then the supervisor shall start the new pause's retry delay at double the previous pause's last delay, capped at 60 minutes.
- [x] **SUPERVISOR-PAUSE-005**: When capture resumes, the supervisor shall log `capture_resumed` and run catch-up for every enabled source.

## Failing jobs

- [x] **SUPERVISOR-FAIL-001**: When all units enqueued by a tick of a job other than a `checks` job (not counting retries) have ended, the supervisor shall count the tick as failed if any ended `failed`, `killed` or `crashed`, as succeeded if all ended `success`, and otherwise (only `busy`, `interrupted`, `needs_auth`, `environment` or `rate_limited` outcomes besides successes) leave the job's consecutive-failure count unchanged.
- [x] **SUPERVISOR-FAIL-002**: When a job's consecutive failed ticks reach the configured threshold (3 by default), the supervisor shall flag the job as failing in `/state/jobs/<source>.<job>.json` and log `job_failing` at ERROR with an action pointing at the job's recent `run_failed` lines; when the job recovers, it shall clear the flag.
- [x] **SUPERVISOR-FAIL-003**: When a tick of a job that had reached the failure threshold succeeds, the supervisor shall log `job_recovered` and reset the count to zero.

## Catch-up

- [x] **SUPERVISOR-CATCHUP-001**: When the supervisor finishes startup, a source resumes from `waiting_for_auth`, a source's rate-limit cooldown ends, or capture resumes from a pause, the supervisor shall fire one tick with trigger `catchup` for each affected job whose last completed tick (all its units ended, whatever their outcomes; a tick with units dropped by emptying the source's queue never completes) is older than the job's interval or that has no completed tick.

## Backfill

- [x] **SUPERVISOR-BACKFILL-001**: The supervisor shall take each `partitioned` job's backfill start to be the operator's `BACKFILL_FROM` date for that job's source when set; otherwise the earliest day on which any of the job's collections has data (as the capture read helpers define data); otherwise the date the source first ran under this `/state`.
- [x] **SUPERVISOR-BACKFILL-002**: When a backfill pass runs for a `partitioned` job, the supervisor shall consider each day from the day before the job's current window back to the backfill start, newest first.
- [x] **SUPERVISOR-BACKFILL-003**: During a backfill pass, the supervisor shall treat a day as missing when it has fewer than three failed backfill attempts and either is in the job's `needs_refill` set, or no collection of the job has data for it (as the capture read helpers define data) and it is not known-empty.
- [x] **SUPERVISOR-BACKFILL-004**: The supervisor shall run the backfill scan outside the scheduling loop, and for each day shall stop checking collections at the first collection that has data.
- [x] **SUPERVISOR-BACKFILL-005**: When a backfill pass finds a missing day, the supervisor shall enqueue one unit with trigger `backfill` for it, subject to SUPERVISOR-QUEUE-005, waiting the job's backfill pause after the previous backfill unit of that job ended.
- [x] **SUPERVISOR-BACKFILL-006**: The supervisor shall start a backfill pass for each `partitioned` job at startup when none is in progress, when its source resumes or capture resumes, and after the job's first successful scheduled tick of each UTC day.
- [x] **SUPERVISOR-BACKFILL-007**: If a backfill pass is requested while one is in progress for the same job, then the supervisor shall start a new pass when the current one finishes.
- [x] **SUPERVISOR-BACKFILL-008**: The supervisor shall save the pass position after each backfill unit, and on startup resume a pass that was in progress from that position.
- [x] **SUPERVISOR-BACKFILL-009**: When a backfill unit ends `success` and afterwards no collection of the job has data for its day, the supervisor shall record the day as known-empty with the date recorded.
- [x] **SUPERVISOR-BACKFILL-010**: When a backfill pass reaches a known-empty day recorded at least 30 days earlier that has not been re-checked, the supervisor shall treat it as missing once; if that unit also records it as known-empty, the supervisor shall keep it known-empty without further re-checks.
- [x] **SUPERVISOR-BACKFILL-011**: When a backfill unit ends other than `success`, `interrupted`, `busy` or `rate_limited`, the supervisor shall count one failed attempt for its day, and when a day reaches three failed attempts the supervisor shall log `backfill_day_abandoned` once.
- [x] **SUPERVISOR-BACKFILL-012**: When the backfill start moves later than a pass's current position, the supervisor shall end that pass.
- [x] **SUPERVISOR-BACKFILL-013**: During a backfill pass, the supervisor shall keep in `/state/backfill/<source>.<job>.json` the pass's counts of days found missing, filled, recorded known-empty and given up.

## Hand-started runs

- [x] **SUPERVISOR-MANUAL-009**: When an operator starts `wearstream run <source> checks` by hand, it shall run with trigger `manual`, take the source's checks lock (exiting with code 5 if it is held), run the checks, record its start and finish in the run log, and skip SUPERVISOR-MANUAL-001 to MANUAL-003, MANUAL-007 and MANUAL-008; it shall not be recorded in `inflight.json` or repaired.
- [x] **SUPERVISOR-MANUAL-010**: When the supervisor finds in the run log the `finish` line of a hand-started run it has not reported, it shall log `manual_run_finished` at INFO with the run's source, job, day and outcome.
- [x] **SUPERVISOR-MANUAL-001**: When an operator starts `wearstream run` by hand for a job other than `checks`, it shall run with trigger `manual`, take the source lock before recording itself, then record itself in `inflight.json` with its pid and pid namespace and in the run log, and when it ends record its finish, update the job's `needs_refill` set by SUPERVISOR-AFTER-006, and remove its `inflight.json` entry.
- [x] **SUPERVISOR-MANUAL-008**: If a hand-started `wearstream run` finds its source recorded as `waiting_for_auth` in `/state/sources/<source>.json`, then it shall exit with code 4 and a message saying to run `wearstream auth <source>`, without running the job; if it finds the source in a rate-limit cooldown, it shall exit with code 6 and the cooldown's end time, without running the job.
- [x] **SUPERVISOR-MANUAL-006**: If an operator starts `wearstream run` by hand for a `partitioned` job without `--day`, or with `--day` for any other job, then it shall exit with code 2 and a usage message without running the job.
- [x] **SUPERVISOR-MANUAL-007**: When a hand-started `wearstream run` has run longer than its job's time limit, it shall stop itself as if it had received `SIGTERM`, and end its process outright if it is still running 45 seconds later.
- [x] **SUPERVISOR-MANUAL-002**: If a hand-started `wearstream run` finds a running daemon whose heartbeat records a capture pause, then it shall exit with code 3 and a message giving the pause reason, without running the job.
- [x] **SUPERVISOR-MANUAL-003**: If a hand-started `wearstream run` finds no running daemon, then it shall run the capture `self_test` on `/data` before running the job, and exit with code 3 if it fails.
- [x] **SUPERVISOR-MANUAL-004**: The supervisor shall not count a run with trigger `manual` as a tick for catch-up or for failing-job counting.
- [x] **SUPERVISOR-MANUAL-005**: Once a minute, the supervisor shall find `manual` entries in `inflight.json` whose source lock it can acquire, and for each shall run the capture `repair` under that lock, record the run `interrupted`, log `inflight_recovered`, and not re-run it.

## State

- [x] **SUPERVISOR-STATE-001**: The supervisor and the child shall write every JSON document under `/state` atomically: to a temporary file, fsynced, renamed into place, and the folder fsynced.
- [x] **SUPERVISOR-STATE-007**: When a source leaves `waiting_for_auth`, its rate-limit cooldown ends, or capture resumes from a pause, the supervisor shall record the time as the source's `resumed_at` in `/state/sources/<source>.json`; when a source unit ends `success`, it shall record the time as the source's `last_source_success_at` there.
- [x] **SUPERVISOR-STATE-002**: The supervisor and the child shall hold `/state/locks/state.lock` around each read-modify-write of a JSON document under `/state`, except documents under `/state/checks/`, which are written only under the source's checks lock, waiting at most 10 seconds; if the wait times out, they shall skip that update and log `state_update_skipped`.
- [x] **SUPERVISOR-STATE-003**: Before starting a child, the supervisor shall add the unit to `inflight.json` and flush it to disk; after the unit has finished and any repair is complete, it shall remove the entry.
- [x] **SUPERVISOR-STATE-004**: When a unit starts, finishes or is repaired, the supervisor shall append a `start`, `finish` or `repaired` line to the run log `runs/<YYYY-MM>.jsonl` for the current UTC month, and shall keep only the 13 most recent monthly files.
- [x] **SUPERVISOR-STATE-005**: If a JSON document under `/state` other than `inflight.json` is unparseable, or is missing while `/state` held a `heartbeat.json` when the supervisor started, then the supervisor shall log `state_document_reset` and treat it as absent; on a `/state` with no earlier heartbeat it shall treat missing documents as absent without logging.
- [x] **SUPERVISOR-STATE-006**: If `inflight.json` is unparseable at startup, then the supervisor shall treat as in flight every unit whose run-log `start` line, in the current or previous month's file, has no `finish` line.

## Lifecycle

- [x] **SUPERVISOR-LIFE-001**: If another process holds `/state/daemon.lock`, then a starting supervisor shall exit with an error saying another daemon is using `/state`.
- [x] **SUPERVISOR-LIFE-002**: When the supervisor starts, it shall, in order: validate configuration, exiting with code 2 before touching `/state` if it is invalid; take `daemon.lock`; read the previous `heartbeat.json` and then write it with phase `starting`; confirm `/state` is writable (exiting if not); run the capture `self_test` on `/data` (starting with capture paused if it fails); set phase `running` and enter the schedule loop; run catch-up; and start backfill passes.
- [x] **SUPERVISOR-LIFE-003**: When the schedule loop first runs after startup, for each source with `daemon` entries in `inflight.json` (or, when it is unparseable, by SUPERVISOR-STATE-006), the supervisor shall record each entry as `interrupted`, repair and re-queue it by SUPERVISOR-AFTER-001 and SUPERVISOR-AFTER-004, and log `inflight_recovered`, before any other unit of that source starts and without holding any other source's queue; it shall leave `manual` entries to SUPERVISOR-MANUAL-005.
- [x] **SUPERVISOR-LIFE-004**: When the supervisor receives `SIGTERM` or `SIGINT`, it shall set phase `stopping`, start no further unit, send `SIGTERM` to every child's process group and to every live hand-started run recorded in `inflight.json` with the supervisor's own pid namespace, wait up to 45 seconds, send `SIGKILL` to any of its children still running, and exit with code 0, leaving its cut-off units in `inflight.json`.

## Health

- [x] **SUPERVISOR-HEALTH-001**: The supervisor's scheduling loop shall rewrite `heartbeat.json` on every pass, at least every 30 seconds, with the daemon pid, version, phase, update time, capture pause reason and next retry, the enabled sources and each one's status.
- [x] **SUPERVISOR-HEALTH-002**: When `wearstream healthcheck` runs, it shall read only `/state` and exit 1 if `heartbeat.json` is missing, unreadable, more than 120 seconds old, or records a capture pause, or the configuration is invalid, and exit 0 otherwise, including while a source is in `waiting_for_auth`.

## Logging

- [x] **SUPERVISOR-LOG-001**: The supervisor shall log each event in the design's logging table at the level given there, with `source` and `job` where they apply and `run_id`, `trigger` and `day` on run events.
- [x] **SUPERVISOR-LOG-002**: When any WearStream process (the daemon, a child, or a hand-started command) logs an event at ERROR, it shall include an `action` field stating in plain language what the operator should do.
- [x] **SUPERVISOR-LOG-003**: When a source unit ends `killed`, the supervisor shall log `run_killed` at ERROR with an action to check network and vendor status or to raise the job's time limit; when a checks unit ends `killed`, the action shall say a check is hanging and to see the last check it logged.
