---
parent: high-level-design
prefix: OPERATOR
---

# Operator interface

## Context and Design Philosophy

WearStream has no UI and no listening port. Everything the operator does goes through three
surfaces, and this component owns all three: **configuration** (environment variables and secret
files), **commands** (`wearstream …`), and **the log** (stdout). The operator is a technically
capable person running a container with Compose, Ansible or plain Docker; they shouldn't need to
read code, know `/state`'s layout, or speak data engineering to run it.

**Principles:**

1. **One namespace, checked strictly.** Every setting is a `WEARSTREAM_` environment variable. An
   unknown `WEARSTREAM_` variable stops startup, naming it, so a typo never silently falls back to a
   default. A setting removed in a later release only warns, so an upgrade never breaks a start.
2. **Secrets are files, never environment values.** Credentials live in `/secrets`. A variable that
   looks like it carries a credential is refused, because environment values leak into `docker
   inspect`, process listings and crash reports.
3. **Overrides can't undo protections.** Every override is bounded by the rules the setting exists
   to serve (Whoop's refresh minute, a source's deadline margin, Whoop's daily quota), not only by
   generic ranges.
4. **The log is written for a person.** Every line has a stable event name, problems that need a
   person say what to do, and nothing in the log is a credential.

## Configuration

### Parsing rules

- A variable that is **set but empty** is treated as unset (Compose and Ansible render empty values
  routinely).
- **Booleans** are `true` or `false`, in any case.
- **Lists** are comma-separated; items are trimmed and lowercased, empty items are dropped, and
  duplicates are ignored.
- **Dates** are `YYYY-MM-DD`; **schedules** are five-field cron expressions (no `@` macros, no
  seconds field), evaluated in UTC.
- **Numbers** are decimal integers in the unit the setting names.

### Paths

| Variable | Default | Meaning |
|---|---|---|
| `WEARSTREAM_DATA_DIR` | `/data` | The capture tree |
| `WEARSTREAM_STATE_DIR` | `/state` | WearStream's state (a local filesystem) |
| `WEARSTREAM_SECRETS_DIR` | `/secrets` | Credentials, per source in `whoop/` and `garmin/` |

These exist for development and tests; operator documentation never mentions them, and in a
container the three are mounts at their defaults. Throughout the designs, `/data`, `/state` and
`/secrets` mean these configured directories, and every path below them (`/state/locks`,
`/secrets/garmin/garmin_tokens.json`, …) is relative to them. The three must resolve to different
folders (compared by device and inode, after following links), none inside another. Some checks
assume a container (Garmin's `off_volume` store warning) and may report outside one.

### Sources

| Variable | Default | Meaning |
|---|---|---|
| `WEARSTREAM_SOURCES` | *(required by `daemon` and `run`)* | The sources to run: `whoop`, `garmin`, or both |

A source not listed is never scheduled, has no checks, and needs no credentials. An unknown name in
the list is an error.

### Schedules, windows and time limits

| Variable | Default | Bounds |
|---|---|---|
| `WEARSTREAM_WHOOP_SCHEDULE` | `17 * * * *` | Used by both `range` and `snapshots`; the minute may not be 55–59 or 0–5 |
| `WEARSTREAM_WHOOP_RANGE_WINDOW_DAYS` | `8` | 1 to 30 |
| `WEARSTREAM_WHOOP_RANGE_TIME_LIMIT` | `5` | Minutes; at least 3 |
| `WEARSTREAM_WHOOP_SNAPSHOTS_TIME_LIMIT` | `5` | Minutes; at least 3 |
| `WEARSTREAM_WHOOP_RANGE_BACKFILL_PAUSE` | `60` | Seconds; at least 30 |
| `WEARSTREAM_WHOOP_CHECKS_SCHEDULE` | `37 * * * *` | |
| `WEARSTREAM_WHOOP_CHECKS_TIME_LIMIT` | `10` | Minutes; at least 3 |
| `WEARSTREAM_GARMIN_DAILY_SCHEDULE` | `30 5 * * *` | |
| `WEARSTREAM_GARMIN_REFERENCE_SCHEDULE` | `30 5 * * *` | |
| `WEARSTREAM_GARMIN_DAILY_WINDOW_DAYS` | `14` | 1 to 60 |
| `WEARSTREAM_GARMIN_DAILY_TIME_LIMIT` | `15` | Minutes; at least 5 |
| `WEARSTREAM_GARMIN_REFERENCE_TIME_LIMIT` | `10` | Minutes; at least 5 |
| `WEARSTREAM_GARMIN_DAILY_BACKFILL_PAUSE` | `60` | Seconds; at least 30 |
| `WEARSTREAM_GARMIN_CHECKS_SCHEDULE` | `7 3-21/6 * * *` | |
| `WEARSTREAM_GARMIN_CHECKS_TIME_LIMIT` | `10` | Minutes; at least 3 |

On top of these, the supervisor's guardrails apply to the result: a single literal minute from 1 to
59; time limits up to 6 hours; a source's work fitting its shortest interval; and every job's
longest gap between runs at most 24 hours (the freshness and token-health tolerances assume at least
daily runs, so an hourly schedule restricted to Mondays is refused). Whoop's two capture jobs share one schedule because one hourly refresh serves both; a
separate minute for each would refresh twice an hour. The time-limit minimums keep each unit longer
than its source's deadline margin (60 seconds for Whoop, 3 minutes for Garmin, 90 seconds for
checks), so a unit always has time to do work.

### Per-source settings

| Variable | Default | Meaning |
|---|---|---|
| `WEARSTREAM_WHOOP_BACKFILL_FROM` | *(unset)* | `YYYY-MM-DD`, not in the future: the earliest day backfill fills; unset means the earliest day already in `/data`, else the first-run date |
| `WEARSTREAM_GARMIN_BACKFILL_FROM` | *(unset)* | As above, for Garmin |
| `WEARSTREAM_WHOOP_REDIRECT_URI` | `http://localhost:8000/callback` | Must match the redirect URI registered with Whoop |
| `WEARSTREAM_WHOOP_REQUESTS_PER_MINUTE` | `60` | 1 to 90 (Whoop allows 100); the unit uses 90% of it |
| `WEARSTREAM_GARMIN_CHINA` | `false` | The account is on Garmin's China site |
| `WEARSTREAM_GARMIN_SELECT` | *(empty: everything)* | Collection names to capture |
| `WEARSTREAM_GARMIN_EXCLUDE` | *(empty)* | Collection names not to capture; wins over the selection |
| `WEARSTREAM_GARMIN_ALT_FORMATS` | `false` | Also download TCX, GPX, KML and CSV for each activity |
| `WEARSTREAM_GARMIN_PACING` | `2` | Seconds between Garmin calls; at least 2 |
| `WEARSTREAM_GARMIN_EXPECTED_EMPTY` | *(the built-in list)* | Collections treated as expected-empty in place of the built-in list (`none` for an empty list); collections flagged `skip_if_empty` or `skip_if_none` are always expected-empty. It changes which check families run and the completeness tolerance |
| `WEARSTREAM_GARMIN_LIBRARY_DEBUG` | `false` | Let the `garminconnect` library log, for troubleshooting only (see *The log*) |

A name in `WEARSTREAM_GARMIN_SELECT`, `_EXCLUDE` or `_EXPECTED_EMPTY` that isn't a Garmin collection
is an error. Check settings such as `recent_folders` are declared by each source, not set by the
operator.

### General

| Variable | Default | Meaning |
|---|---|---|
| `WEARSTREAM_LOG_FORMAT` | `console` | `console` (human-readable) or `json` (JSON Lines) |
| `WEARSTREAM_LOG_LEVEL` | `INFO` | `INFO` or `DEBUG`; higher levels are not offered, because recovery and resume events at INFO are what close log-based alerts |
| `WEARSTREAM_FAILING_TICKS` | `3` | Consecutive failed ticks before `job_failing`; 1 to 100 |
| `WEARSTREAM_UMASK` | `022` | The file-creation mask every process sets at startup, as three or four octal digits from `000` to `077` (the owner's own permissions are never masked); `077` keeps captures readable only by the container's uid |

### Which settings each command needs

| Command | Reads |
|---|---|
| `daemon`, `run` | Everything; `WEARSTREAM_SOURCES` is required, and `run` for a source not in it exits `2` |
| `auth <source>` | Paths, logging, and that source's settings including its time limits, whether or not the source is in `WEARSTREAM_SOURCES` |
| `accept-schema <source>` | Paths, logging, the source's checks time limit, and for Garmin its selection lists |
| `status`, `healthcheck` | Paths and logging only |
| `config` | Everything, reporting problems instead of stopping |
| `version`, `--help` | Nothing; never fail on configuration or the environment |

### Override errors and leftovers

- An override for a job or source that doesn't take it (`WEARSTREAM_WHOOP_SNAPSHOTS_WINDOW_DAYS`) is
  an error.
- An override for a source that isn't in `WEARSTREAM_SOURCES` is ignored by the daemon, which logs
  `setting_ignored` (WARNING) once at startup, so a source can be switched off without deleting its
  settings. `auth` for that source still reads them.
- A **retired** setting (one an earlier release used) is ignored, and the daemon logs
  `setting_retired` (WARNING, action: remove it) once at startup.

### Secret files

| File | Needed when |
|---|---|
| `/secrets/whoop/client_id`, `/secrets/whoop/client_secret` | Whoop is enabled |
| `/secrets/garmin/email`, `/secrets/garmin/password` | Optional; for unattended Garmin password sign-in |

Tokens and sessions are written by `wearstream auth` and the sources, so `/secrets/<source>/` must be
writable by the container user. Docker or Compose secrets can supply the static files by mounting
them **inside** that folder (for example Compose `secrets: - source: whoop_client_secret,
target: /secrets/whoop/client_secret`); Docker's own `/run/secrets` is read-only and can't hold
tokens.

**Refused environment.** Every process except `version` and `--help` refuses to start when the
environment holds a variable whose name, in any case, starts with `WEARSTREAM_`, `WHOOP_` or
`GARMIN` and contains `SECRET`, `PASSWORD`, `PASSWD`, `TOKEN` or `API_KEY`, unless it is a current or
retired WearStream setting (`WEARSTREAM_SECRETS_DIR`, for example) or `GARMINTOKENS` (which the
Garmin source removes and warns about). The error names the variable and says to remove it: WearStream reads credentials only from
files under `/secrets`. This also catches leftovers from other tools (`WHOOP_TOKEN_PATH`,
`WHOOP_CLIENT_SECRET`), whose meaning WearStream would otherwise silently ignore.

### Validation

A process reads its configuration once, before anything else (before taking any lock or writing
any state), and validates all of it, reporting every problem in one error so the operator fixes
them in one pass. Validation includes every guardrail the other components define (the supervisor's
schedule, time-limit, interval and queue rules, the Whoop schedule rule, the check settings), so
`wearstream config` reports all of them too. An invalid configuration logs `config_invalid` at
ERROR, listing the problems, with the action to fix them (`wearstream config` shows them), and exits
with code `2`, except `healthcheck` (below).

## Commands

| Command | Purpose | Exit codes |
|---|---|---|
| `wearstream daemon` | Run the supervisor (the container's entry point) | `0` after a clean stop; `1` startup failure; `2` invalid configuration |
| `wearstream run <source> <job> [--day D]` | Run one unit by hand | As the supervisor's child contract |
| `wearstream auth whoop` / `auth garmin` | Get a credential, interactively | As each source's auth design |
| `wearstream accept-schema <source> <collection>` / `--all` | Accept a payload shape change | As the checks design |
| `wearstream status` | Show what WearStream is doing and what needs attention | See below |
| `wearstream config` | Show the effective configuration and whether it is valid | `0` valid; `2` invalid |
| `wearstream healthcheck` | The container health check | `0` healthy; `1` otherwise, including invalid configuration (Docker reserves `2`) |
| `wearstream version` | Print the version | `0` |

Every command accepts `--help`. An interactive command (`auth`) with no terminal on its input exits
`2` with a message to run it with `docker exec -it` or `docker run -it`.

**Where command output goes.** Commands print plain sentences to the terminal they were run from.
Their structured events go to that same stdout, which for `docker exec` is the operator's session,
not the container's log. So the daemon logs what it observes of them: `manual_run_finished` (with
the outcome) when a hand-started run's finish appears in the run log, `source_resumed` when a new
credential appears, and the next checks run's results after `accept-schema`.

### `wearstream status`

Reads only `/state` (never the network or `/data`) and prints, in this order:

1. **Daemon:** running, or not running (no heartbeat, a heartbeat older than 120 seconds, or one that
   can't be read), its phase and version, and whether capture is paused and why.
2. **Needs attention:** every condition `/state` records that needs a person, for the sources the
   heartbeat lists as enabled, each with its action: the daemon not running; capture paused; a
   source waiting for a credential; a source flagged as repeatedly rate-limited; a job flagged as
   failing; checks failing at error severity and not suppressed. Conditions the
   log reports only as single events (a killed unit) appear here only through what they lead to
   (a failing job). Empty when nothing needs a person.
3. **Per source:** state (active, waiting for a credential since when, in a cooldown until when), last
   successful unit, each job's last completed tick and outcome, units in flight, backfill (pass
   position, days missing, filled, known-empty and given up).
4. **Checks:** checks warning or failing, and checks suppressed, with since when.

A state document that can't be read is shown as unreadable, and counts as needing attention. `--json`
prints the same as one JSON object with a `schema_version`; fields are only added within a schema
version. The exit code is `0` when "needs attention" is empty, `1` when it isn't, and `3` when `/state`
holds no state (WearStream has never run there).

### `wearstream config`

Prints every setting with its effective value and its origin (environment or default), which secret
files are present (never their contents), retired or ignored settings, and every validation problem.
It doesn't contact any vendor.

## The log

### Destination and shape

Every WearStream process logs to **stdout**, unbuffered, one event per line. A line is at most 4 KiB,
because only writes up to that size reach a pipe without interleaving with other processes' lines.
A longer event is shortened without breaking it: the envelope (`timestamp`, `level`, `event`,
`component`), the reserved fields and `action` are always kept, the longest other values are cut
first, and `truncated=true` is added, so a JSON line stays valid JSON. Newlines inside values (a
traceback) are escaped, so an event is always one line.

Before anything else is imported, every process routes **stderr, Python warnings and uncaught
exceptions** through the same logger, as events `stderr`, `python_warning` and `uncaught_exception`
(the last at ERROR, with an action to report the problem), and library loggers it lets through as
`library_log`, so nothing bypasses the formatting and redaction below and every line has an event
name.

- **`console`** (default): `2026-11-23T14:17:02.123Z INFO run_finished component=supervisor
  source=whoop job=range day=2026-11-23 duration_s=12.1 written=3`. Values with spaces or quotes are
  double-quoted with escapes; control characters are escaped. No color codes unless stdout is a
  terminal.
- **`json`**: one JSON object per line with the same fields; `timestamp` is ISO 8601 UTC with
  milliseconds.

Every line carries `timestamp`, `level`, `event` and `component` (`supervisor`, `capture`, `checks`,
`whoop`, `garmin`, `operator`). A child's lines also carry the unit's `run_id`, `source`, `job`,
`trigger` and, for a partitioned unit, `day`; capture events carry `dt`, the folder they wrote. These
are **reserved field names**: no event uses them for anything else.

### Events

- **Names are stable** `snake_case` identifiers, documented with their component, so alert rules can
  match them across releases; renaming one is a breaking change. Alert rules should match `event`
  together with `component`.
- **Levels:** `ERROR` when a person must act; `WARNING` for a problem WearStream is handling or that
  may need attention if it persists; `INFO` for what WearStream did (units, passes, transitions,
  recoveries); `DEBUG` for detail (individual captures).
- **Every `ERROR` carries `action`**: one plain-language sentence saying what to do.
- **The daemon's startup line** (`daemon_started`) lists the version, the enabled sources and each
  job's effective schedule, so the log records how WearStream was configured.

### Errors, tracebacks and credentials

- **Tracebacks are rendered without local variables**, always, in every process: the backstop that
  keeps a variable holding a token out of the log.
- **Credential paths** (owned by the sources) log only an exception's type, never its text or
  traceback.
- In any process that loads a source, an exception raised from a source's modules or from the HTTP
  and Garmin libraries (`httpx`, `httpcore`, `curl_cffi`, `garminconnect`) is logged by its type
  only, wherever it is finally caught, because a top-level handler can't tell whether it crossed a
  credential path.
- Any other unexpected exception logs its type and its message truncated to 300 characters.
- At `DEBUG`, a traceback is added: frames and exception types only, never exception messages, for
  every exception in the chain.
- **A last-line redaction filter** checks every field name, case-insensitively and recursively into
  nested values, against the capture component's secret-name list (`password`, `passwd`, `secret`,
  `token`, `apikey`, `api_key`, `authorization`, `credential`, `private_key`, `privatekey`,
  `signature`, `sessionid`, `session_id`) plus `cookie`, and replaces a matching field's value with
  `[redacted]`. Event fields are named so that none matches the list (a pagination token is logged as
  `cursor`, a token-store path as `store_path`, a credential fingerprint as `fingerprint`), and a test
  enforces it, so the filter only ever acts on a mistake. Payload key names and file paths travel as
  list values, never as field names.
- **Library loggers** (`httpx`, `httpcore`, `curl_cffi`, `garminconnect`) are set to `WARNING` or
  silenced as each source requires. With `WEARSTREAM_GARMIN_LIBRARY_DEBUG=true`, the `garminconnect`
  loggers are let through and the daemon logs `library_debug_enabled` at WARNING on every start,
  saying that library output may include request details and tokens and should be turned off after
  troubleshooting; it is the one documented exception to principle 4, and the HLD records it.

## Decisions & Alternatives

| Decision | Chosen | Alternatives Considered | Rationale |
|---|---|---|---|
| Configuration source | Environment variables, plus secret files | A configuration file; command-line flags | Environment variables are how Compose, Ansible and Docker all configure containers; a file is one more mount to manage. |
| Unknown and retired variables | Unknown stops startup; retired warns | Ignore unknown names; fail on retired ones | A misspelled override silently falling back is the hardest misconfiguration to notice, but an upgrade that removes a setting must not break a running deployment. |
| Secrets | Files only; secret-looking variables refused with "remove it" | Allow environment values; support `_FILE` variables | Environment values leak into `docker inspect` and process listings. Fixed file locations under `/secrets` need no variable at all, and Compose secrets can target them. |
| Whoop schedule | One setting for both capture jobs; minutes 55–05 refused | One per job; any minute | Separate minutes double the hourly refreshes; the top of the hour is where grants were lost. |
| Capture interval | At most 24 hours | Any interval | Freshness and token-health tolerances assume at least daily capture. |
| Time-limit minimums | Per source and kind, above the deadline margins | One global minimum | A limit inside a source's deadline margin makes every unit start nothing and fail. |
| Pacing bounds | Whoop backfill pause ≥ 30 s, requests per minute ≤ 90, Garmin pacing ≥ 2 s | Unbounded | The Whoop quota argument and Garmin's burst sensitivity depend on them. |
| Log level | `INFO` or `DEBUG` only | Any level | Recovery events at INFO close log-based alerts; hiding them leaves alerts open forever. |
| Path variables | Development and tests only; derived paths follow them | Operator-facing | Moving mounts inside a container adds failure modes and nothing an operator needs. |
| Validation order | Before any lock or state write | After taking the daemon lock | A misconfigured start shouldn't leave a heartbeat or hold a lock. |
| Health check exit codes | `0` or `1` only | Share the `2` used for invalid configuration | Docker reserves `2` for health checks. |
| Log destination | stdout for every process, with stderr routed through the logger | stderr for errors; files under `/state` | Container runtimes collect stdout; one stream keeps related lines together, and routing stderr keeps warnings and crashes inside the redaction rules. |
| Line size | At most 4 KiB, truncated with a marker | Unlimited | Longer writes to a pipe can interleave with other processes' lines. |
| Hand-started command output | The daemon logs what it observes of them | Rely on the command's own output | `docker exec` output never reaches the container log or an aggregator. |
| Status exit code | `0` nothing needs attention, `1` something does, `3` no state | Always `0` | Lets a script or a monitoring probe ask "does anything need me?"; a dead daemon is one of those things. |
| Redaction filter | Capture's secret list plus `cookie`, recursive, case-insensitive, with event field names tested against it | A shorter list; scanning values | Field names are reliable to match; one list keeps capture and logging consistent. |

## Open Questions & Future Decisions

### Deferred
1. Metrics and push notifications (issues #3 and #4) would add settings under the same namespace and
   read the same events.

## References

- `docs/high-level-design.md`: commands, mounts, "The operator log is the interface"
- `docs/intent/supervisor/supervisor-design.md`: guardrails, `/state`, logging table, healthcheck
- `docs/intent/whoop/`, `docs/intent/garmin/`: each source's settings and credential-safe logging
- `docs/intent/checks/checks-design.md`: check events, `accept-schema`
