# Operator interface — EARS specs

Design: [operator-design.md](operator-design.md). Prefix `OPERATOR`. Facets: `PARSE`, `PATH`,
`SET`, `ENV`, `VAL`, `CMD`, `STATUS`, `CONFIG`, `LOG`, `REDACT`.
"A process" is any WearStream process: the daemon, a child, or a command. "The settings" are the
`WEARSTREAM_` variables in the design's configuration tables.

## Parsing

- [x] **OPERATOR-PARSE-001**: A process shall treat a `WEARSTREAM_` variable that is set to an empty string as unset.
- [x] **OPERATOR-PARSE-002**: A process shall accept `true` and `false` in any case for a boolean setting, and report any other value as invalid.
- [x] **OPERATOR-PARSE-003**: A process shall split a list setting on commas, trim and lowercase each item, drop empty items and ignore duplicates.
- [x] **OPERATOR-PARSE-004**: A process shall accept only five-field cron expressions for schedule settings, `YYYY-MM-DD` for date settings, and decimal integers for numeric settings, and report any other form as invalid.

## Paths

- [x] **OPERATOR-PATH-001**: A process shall take the data, state and secrets directories from `WEARSTREAM_DATA_DIR`, `WEARSTREAM_STATE_DIR` and `WEARSTREAM_SECRETS_DIR`, defaulting to `/data`, `/state` and `/secrets`, and resolve every path the designs write under `/data`, `/state` or `/secrets` relative to the configured directory.
- [x] **OPERATOR-PATH-002**: If two of the three directories resolve to the same folder (by device and inode, after following links) or one lies inside another, then a process that uses them shall report the configuration invalid.

## Settings

- [x] **OPERATOR-SET-001**: The daemon and `wearstream run` shall require `WEARSTREAM_SOURCES` to list one or both of `whoop` and `garmin`, and report any other name as invalid; `wearstream run` for a source not in the list shall exit with code 2.
- [x] **OPERATOR-SET-002**: A process shall read schedule, window, time-limit and backfill-pause overrides only from the variables named in the design's schedules table, apply them to the sources' job declarations, and report as invalid an override below its stated minimum or outside its stated range.
- [x] **OPERATOR-SET-003**: A process shall report as invalid an override for a job or setting that the named source doesn't have, or that doesn't apply to that job's kind.
- [x] **OPERATOR-SET-004**: When a setting names a source not listed in `WEARSTREAM_SOURCES`, the daemon shall ignore it and log `setting_ignored` at WARNING naming it once at startup; `wearstream auth` for that source shall still read it.
- [x] **OPERATOR-SET-005**: When a process finds a retired setting, it shall ignore it, and the daemon shall log `setting_retired` at WARNING with the action to remove it once at startup.
- [x] **OPERATOR-SET-006**: A process shall report as invalid any `WEARSTREAM_` variable that is neither a current setting nor a retired one, naming it.
- [ ] **OPERATOR-SET-007**: A process shall report as invalid a name in `WEARSTREAM_GARMIN_SELECT`, `WEARSTREAM_GARMIN_EXCLUDE` or `WEARSTREAM_GARMIN_EXPECTED_EMPTY` that is not a Garmin collection, except the value `none` in `WEARSTREAM_GARMIN_EXPECTED_EMPTY`, which means an empty list.
- [x] **OPERATOR-SET-008**: A process shall report as invalid a `BACKFILL_FROM` date in the future, a `WEARSTREAM_WHOOP_REQUESTS_PER_MINUTE` outside 1 to 90, a `WEARSTREAM_GARMIN_PACING` below 2, a `WEARSTREAM_FAILING_TICKS` outside 1 to 100, and a `WEARSTREAM_LOG_LEVEL` other than `INFO` or `DEBUG`.
- [x] **OPERATOR-SET-009**: Each command shall read only the settings the design's per-command table assigns it (`auth` its source's settings and time limits whatever `WEARSTREAM_SOURCES` says; `accept-schema` its source's checks time limit and Garmin selection lists), and `version` and `--help` shall never fail on configuration or the environment.

- [x] **OPERATOR-SET-010**: Every process shall set its file-creation mask from `WEARSTREAM_UMASK` (default `022`) before creating any file, and report as invalid a value that is not three or four octal digits from `000` to `077`.

## Refused environment

- [x] **OPERATOR-ENV-001**: If the environment holds a variable whose name, compared case-insensitively, starts with `WEARSTREAM_`, `WHOOP_` or `GARMIN` and contains `SECRET`, `PASSWORD`, `PASSWD`, `TOKEN` or `API_KEY`, and is not a current or retired WearStream setting or `GARMINTOKENS`, then every process other than `version` and `--help` shall refuse to start, naming the variable and saying to remove it because credentials are read only from files under `/secrets`, exiting with code 2 (`wearstream healthcheck` with code 1).

## Validation

- [x] **OPERATOR-VAL-001**: A process shall read and validate its configuration before taking any lock or writing anything under `/state`.
- [x] **OPERATOR-VAL-002**: Configuration validation shall include every guardrail the other components define on settings (SUPERVISOR-JOB-002 and JOB-003, WHOOP-CAPTURE-JOB-004, CHECKS-SET-001), and if a process finds its configuration invalid, then it shall log `config_invalid` at ERROR listing every problem found, with the action to fix them and that `wearstream config` shows them, and exit with code 2, except `wearstream healthcheck`, which shall exit with code 1.

## Commands

- [ ] **OPERATOR-CMD-001**: The `wearstream` entry point shall provide `daemon`, `run`, `auth`, `accept-schema`, `status`, `config`, `healthcheck` and `version`, each accepting `--help`.
- [ ] **OPERATOR-CMD-002**: If an interactive command (`auth`) finds no terminal on its input, then it shall exit with code 2 and a message to run it with `docker exec -it` or `docker run -it`.
- [x] **OPERATOR-CMD-003**: `wearstream version` shall print the installed version and exit with code 0.

## `wearstream status`

- [ ] **OPERATOR-STATUS-001**: When `wearstream status` runs, it shall read only `/state`, never the network or `/data`.
- [ ] **OPERATOR-STATUS-002**: `wearstream status` shall print, in order: the daemon's state (running, or not running when the heartbeat is missing, unreadable or more than 120 seconds old), phase, version and any capture pause; the conditions needing attention, each with its action; each source's state, last successful unit, jobs' last completed ticks and outcomes, units in flight and backfill progress; and the checks warning, failing or suppressed.
- [ ] **OPERATOR-STATUS-003**: For the sources the heartbeat lists as enabled, `wearstream status` shall list as needing attention: the daemon not running; capture paused; a source waiting for a credential; a source flagged as repeatedly rate-limited; a job flagged as failing; a check failing at error severity and not suppressed; and a state document it cannot read.
- [ ] **OPERATOR-STATUS-004**: `wearstream status` shall exit with code 0 when nothing needs attention, 1 when something does, and 3 when `/state` holds no state.
- [ ] **OPERATOR-STATUS-005**: Where `--json` is given, `wearstream status` shall print the same information as one JSON object carrying a `schema_version`.

## `wearstream config`

- [x] **OPERATOR-CONFIG-001**: When `wearstream config` runs, it shall print every setting with its effective value and origin (environment or default), which secret files are present without their contents, any retired or ignored settings, and every validation problem, without contacting a vendor, and exit with code 0 when valid and 2 when not.

## The log

- [x] **OPERATOR-LOG-001**: Every process shall write its log to stdout, unbuffered, one event per line with newlines inside values escaped, each line written with a single write of at most 4 KiB; an event that would be longer shall keep `timestamp`, `level`, `event`, `component`, the reserved fields and `action`, shorten its longest other values first, and add `truncated=true`, so a JSON line stays valid JSON.
- [x] **OPERATOR-LOG-002**: Before importing anything else, every process shall route stderr, Python warnings, uncaught exceptions and any library logger it lets through into its logger as the events `stderr`, `python_warning`, `uncaught_exception` (at ERROR, with an action to report the problem) and `library_log`.
- [x] **OPERATOR-LOG-003**: Where `WEARSTREAM_LOG_FORMAT` is `console` (the default), a process shall write each line as the UTC timestamp with milliseconds, the level, the event name and `key=value` fields, double-quoting values that contain spaces or quotes, escaping control characters, and using color codes only when stdout is a terminal; where it is `json`, it shall write one JSON object per line with the same fields.
- [x] **OPERATOR-LOG-004**: Every log line shall carry `timestamp`, `level`, `event` and `component`, and a child's lines shall also carry its unit's `run_id`, `source`, `job`, `trigger` and, for a partitioned unit, `day`; no event shall use these names, or `dt`, for anything else.
- [ ] **OPERATOR-LOG-005**: When the daemon starts, it shall log `daemon_started` with the version, the enabled sources and each job's effective schedule.
- [x] **OPERATOR-LOG-006**: Every process shall render tracebacks without local variables.
- [x] **OPERATOR-LOG-007**: If a process that loads a source catches an exception raised from a source's modules or from `httpx`, `httpcore`, `curl_cffi` or `garminconnect`, then it shall log only the exception's type; if it catches any other unexpected exception, it shall log the type and the message truncated to 300 characters.
- [x] **OPERATOR-LOG-010**: Where the log level is `DEBUG`, a process shall add to an exception's event a traceback of frames and exception types for every exception in the chain, without any exception message or local variable.
- [x] **OPERATOR-LOG-008**: A process shall set the `httpx`, `httpcore` and `curl_cffi` loggers to `WARNING`, and the `garminconnect` loggers as the Garmin source requires.
- [ ] **OPERATOR-LOG-009**: Where `WEARSTREAM_GARMIN_LIBRARY_DEBUG` is `true`, the daemon shall log `library_debug_enabled` at WARNING on every start, saying that library output may include request details and tokens and should be turned off after troubleshooting.

## Redaction

- [x] **OPERATOR-REDACT-001**: Before writing an event, a process shall replace with `[redacted]` the value of every field, at any depth, whose name contains case-insensitively `password`, `passwd`, `secret`, `token`, `apikey`, `api_key`, `authorization`, `credential`, `private_key`, `privatekey`, `signature`, `sessionid`, `session_id` or `cookie`.
- [ ] **OPERATOR-REDACT-002**: No event defined by WearStream shall have a field whose name matches the list in OPERATOR-REDACT-001 (a pagination token is logged as `cursor`, a token-store path as `store_path`, a credential fingerprint as `fingerprint`), and payload key names and file paths shall travel as list values, never as field names.
