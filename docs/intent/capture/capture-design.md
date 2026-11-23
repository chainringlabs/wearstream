---
parent: high-level-design
prefix: CAPTURE
---

# Capture

## Context and Design Philosophy

The capture tree under `/data` is the durable record of exactly what each source returned.
It is WearStream's only output and the person's source of truth: anything built from it (a
Parquet export, an analysis notebook, another tool) is derived and rebuildable, and never
modifies it. Raw responses are kept once, unaltered, so they can be reprocessed later without
re-fetching from rate-limited sources that may restate or drop history.

This component owns the contract of that tree: what goes in, how files are named and laid
out, what the sidecar records, how writes stay atomic and append-only, how a re-capture of
identical bytes is skipped, what counts as a capture when anything reads the tree back, and
how an interrupted write is cleaned up. One function writes it, `capture(...)`, and every
source captures through it.

The tree is byte-compatible with the bronze layer grecohome-dagster writes (sidecar
`schema_version` `v1`), so an existing tree mounted as `/data` is continued, not replaced.

## What to capture

The tree holds a source's **functional domain data**: the records anyone would want to
reprocess. It is not a log of every byte exchanged with the source. Each source decides **per
endpoint** what counts as data; the writer doesn't infer it.

- **Capture:** domain records (sleep, recovery, workouts, daily summaries, activity files) and
  reference endpoints that give real context (profile, devices, units). Each reference
  endpoint gets **its own collection** (for example `whoop/profile`, `garmin/devices`).
- **Skip, because it's pointless:** protocol plumbing (keep-alives, `204`, redirects), empty
  pagination terminators, and handshake responses that carry no domain data.
- **Never capture, because it's a security rule:** OAuth token, refresh and authorization
  responses; API keys, client secrets, bearer or session tokens and passwords, in any payload,
  header or field; signed URLs that embed credentials. The tree is append-only and likely
  backed up, so a secret written there can't easily be purged. If a data payload echoes a
  secret, that field is removed before capture (see *Secret screening*). This is the **one**
  permitted change to a payload. Secrets never go into the sidecar's `request_url` or
  `request_params` either; keeping them out is the calling source's responsibility, proven by
  that source's tests.
- **Error responses are not captured.** When the caller records an `http_status`, only a 2xx
  response is written; anything else is refused (see *Guards*) and the source logs the failure.
  An error envelope stored as a payload would look like data to every reader.

## Writing rules

- **Bytes only.** The payload is written byte-for-byte as the caller hands it over, never as a
  decoded string. Decoding passes data through character-set assumptions, and a mojibake bug at
  capture time would corrupt the record irreversibly.
- **Content encoding is resolved deliberately.** Transport compression (a server gzipping JSON
  for the trip, which the HTTP client undoes transparently) is stored **decompressed**, with the
  arrival encoding recorded in `content_encoding`. Native compressed formats (a ZIP of an
  activity file) are stored **as received**, with the extension reflecting it. The declared
  charset is recorded in `charset`. Nothing is compressed at rest, so `stored_encoding` is always
  `identity`.
- **Append-only.** Every capture is a new, uniquely named pair of files. The writer never edits,
  overwrites or deletes a capture; the only files it removes are its own temporary files, and a
  payload it linked moments earlier in the same call when the sidecar could not follow.
- **Never overwrite, even by accident.** Each file is moved into place with a **hard link**,
  which fails if the name exists. On a name collision at either link the writer removes what it
  linked in this attempt, draws a new `short_id`, and tries again, up to five attempts.
- **Atomic, and paired as tightly as the filesystem allows.** The sequence is:
  1. Create any missing folders on the path, and `fsync` the parent of each folder created.
  2. Write the payload and the sidecar to hidden temporary files (`.tmp_<hex>`) in the
     destination folder and `fsync` both.
  3. Link the payload into place, then the sidecar.
  4. `fsync` the destination folder.
  5. Remove both temporary files. They are removed on every path out of the call, success or
     failure.

  Readers skip dotfiles, so a half-written file is never seen. Preparing both files before
  linking either shrinks the window in which a payload exists without its sidecar to the gap
  between two `link` calls.
- **A durable pair is a written capture.** Once both links succeed the capture is `written`.
  If the folder `fsync` in step 4 then fails, the outcome stays `written` and the writer logs a
  warning: the pair is in place, and reporting it as failed would understate the run.
- **Roll back a lone payload.** If linking the sidecar fails for any reason other than a name
  collision, the writer removes the payload it just linked and reports the capture as failed.
  The tree then never holds a payload without provenance through a failure the writer can see.
  Only a process killed between the two links can still leave a lone payload (see *Interrupted
  writes*).
- **Non-fatal for ordinary errors.** `capture` never raises an ordinary error (`Exception`):
  it logs and reports a `failed` outcome, so the run continues with its other requests and
  reports the failure in its totals. It does not swallow process-level interrupts
  (`KeyboardInterrupt`, `SystemExit`); those propagate after the temporary files are removed,
  so a stop or kill signal still ends the process.
- **Hard links are required.** The writer has no non-atomic fallback. `self_test(root)` checks,
  before any capture, that the root accepts a temporary file, a hard link, an `fsync` of a
  folder, and removal of both, using dot-named files that no reader sees. A failure is reported
  with the failing step and the operating-system error.
- **Permissions follow the process umask.** Folders and files are created with the default
  modes filtered by the process umask (`0755` and `0644` under the common `022`). The operator
  controls privacy through the container user and umask; the writer does not override it.
- **The root is passed in.** The caller supplies the data root (`/data` in the container);
  nothing below the writer hardcodes it, which keeps another storage backend possible.

## Layout and naming

```
{root}/{source}/{collection}/dt={YYYY-MM-DD}/
    {collection}_{fetched_unix_ms}_{short_id}.{ext}
    {collection}_{fetched_unix_ms}_{short_id}.meta.json
```

| Segment | Meaning |
|---|---|
| `source`, `collection` | Lowercase provider and dataset names (`whoop`, `sleep`) |
| `dt=` | The **partition day** the caller passes: the UTC day of the fetch window. When none is passed, the UTC date of the fetch time |
| `fetched_unix_ms` | Capture time, Unix epoch milliseconds, UTC, in decimal digits |
| `short_id` | 6 random lowercase hex characters (3 bytes) |
| `ext` | The **stored** form. An explicit `ext` from the caller is used as given. Otherwise it is derived from `content_type` (parameters dropped, trimmed, lowercased): `application/json`/`text/json` → `json`, `text/csv`/`application/csv` → `csv`, `application/xml`/`text/xml` → `xml`, `text/plain` → `txt`, anything else → `bin` |

**Names are validated before anything is written.** `source` and `collection` must match
`[a-z0-9_-]+`, `dt` must be a real calendar date written `YYYY-MM-DD`, and `ext` must match
`[a-z0-9]+(\.[a-z0-9]+)*` and must not be `meta.json` or end in `.meta.json`. Every path the
writer creates therefore stays inside the root, a payload name can never equal a sidecar name,
and every name is safe as an object-store key.

`fetched_at` is formatted `YYYY-MM-DDTHH:MM:SS.mmmZ`.

## Sidecar (`.meta.json`)

Every payload has a sidecar carrying the provenance the bytes alone don't. It is compact JSON
with sorted keys.

| Set by the writer (overrides any caller value) | Passed through from the caller |
|---|---|
| `source`, `collection`, `fetched_at`, `fetched_at_unix_ms`, `byte_size`, `sha256` (of the stored bytes), `stored_encoding` (`identity`), `schema_version` (`v1`), `processor` (`wearstream`), `processor_version` (the installed WearStream version) | `request_url`, `request_params` (enough to understand or replay the fetch, **never** secrets), `http_status`, `content_type`, `charset`, `content_encoding`, and source-specific fields such as `capture_mode` and `redacted_fields` |

`sha256` and `byte_size` describe the bytes on disk, so integrity can be verified later. A
non-empty `redacted_fields` flags that the payload isn't byte-identical to what arrived and
names what was removed. The schema is open: extra source-specific keys are allowed, and
readers must ignore keys they don't know.

## What counts as a capture

Anything that reads the tree back (skip-identical, backfill's "does this day have data",
checks, a future export) uses this one definition.

A file in `{root}/{source}/{collection}/dt={day}/` is **name-conforming** when its name is
`{collection}_{ms}_{id}.{ext}` with the folder's own `collection`, `ms` one or more decimal
digits, `id` six lowercase hex characters, and `ext` as validated above. The name is parsed
from the right, because collection names contain underscores. The **stem** is
`{collection}_{ms}_{id}`. A name-conforming file whose `ext` is `meta.json` is a sidecar; any
other is a payload.

A **capture** is a stem with exactly one payload and one sidecar, where the sidecar parses as a
JSON object whose `sha256` is a string and whose `source` and `collection` equal the folder's.
Everything else in the tree is not a capture and is ignored by every reader in WearStream:

- files whose names start with `.` (temporary files, editor and OS litter);
- files and folders that are not name-conforming, folders named `dt=` with an invalid date, and
  subfolders inside a `dt=` folder;
- a lone payload, a lone sidecar, or a stem with more than one payload;
- a pair whose sidecar is unreadable, has no string `sha256`, or names a different source or
  collection.

A `dt=` folder **has data** when it holds at least one capture.

**Order.** Captures are ordered by `ms`, newest first; equal `ms` values are ordered by stem,
the lexicographically greater stem counting as newer. The order is deterministic, though a tie
is resolved arbitrarily in time. `ms` comes from the host clock, so a backward clock step can
make a later capture sort as older; container clocks follow host time synchronisation, where
such steps are rare and small, and this is accepted rather than adjusting recorded fetch times.

The component provides the read helpers the rest of WearStream uses: list the captures of a
`(source, collection, dt)` folder newest first, and tell whether a folder has data.

## Skip-identical

With `dedupe=True` (the default), the writer computes the payload's sha256 and compares it with
the `sha256` of the newest earlier capture **in the same `(source, collection, dt)` folder**. If
they are equal, nothing is written and the outcome is `skipped_identical`. A source that
re-captures a trailing window then costs API calls but no storage unless it returned something
new. Which earlier capture counts depends on the scope:

- **`dedupe_scope="folder"`** (the default): the newest capture in the folder. Right when **one
  request** writes the folder per run.
- **`dedupe_scope="request"`**: the newest capture in the folder **whose `request_params` equal
  this capture's**. Right when **several requests** write one folder (one per activity, device
  or goal status): each request is compared only with its own earlier captures.

`request_params` are compared in canonical JSON: keys sorted, separators `,` and `:` with no
spaces, and any value JSON cannot encode rendered with `str()`. This encoding is fixed, because
it must match how existing sidecars compare.

In both scopes:

- **Only identical bytes are skipped**, so skip-identical never loses data.
- **Only captures count.** Anything that is not a capture by the definition above is passed
  over. If the folder has no matching capture, the payload is written.
- **Callers choose per call** (`dedupe=False` keeps every capture). Which endpoints use which
  setting is each source's decision, recorded in its capture design.
- **Folders are indexed once per process.** The first capture into a folder reads that folder's
  captures; later captures in the same process use and extend that index. One writer per
  source at a time is guaranteed by the supervisor, so the index stays accurate for the run.

## Secret screening

`screen_secrets(obj)` takes a parsed JSON value and returns a copy with every key whose name
looks like a credential removed, plus the dotted paths of what it removed (for example
`profile.accessToken`, `devices[0].authToken`). Matching is a case-insensitive substring test
against a deliberately broad list (`password`, `passwd`, `secret`, `token`, `apikey`,
`api_key`, `authorization`, `credential`, `private_key`, `privatekey`, `signature`,
`sessionid`, `session_id`). Over-redaction is cheap and under-redaction is not recoverable.

A source applies it to the endpoints it flags, before serializing the payload, and records the
paths in the sidecar's `redacted_fields`. Screening only works on payloads the source parses
and reserializes; a source capturing raw response bytes must not capture an endpoint that can
echo secrets.

## Guards

The guards run in this order, before anything is written:

1. **Names.** Invalid `source`, `collection`, `dt` or `ext` (see *Layout and naming*): outcome
   `failed`.
2. **Type.** A payload that isn't `bytes` or `bytearray`: outcome `failed`.
3. **Status.** An `http_status` that is present but is not an integer, or is a boolean: outcome
   `failed`. An integer outside 200–299: nothing is written, outcome `skipped_non_2xx`. A caller
   that records no status is unaffected.
4. **Skip-identical**, as above: outcome `skipped_identical`.

## Failures that need a person

Some write failures are about the environment, not the capture, and every later capture will
fail the same way: no space left (`ENOSPC`, `EDQUOT`), a read-only filesystem (`EROFS`), or a
permission or operation refusal (`EACCES`, `EPERM`). The writer reports these with
`error_kind` `environment`; every other failure has `error_kind` `other`. The run that receives
an `environment` failure stops capturing and reports it, so the operator gets one clear error
naming the action ("free space on /data", "make /data writable by the container user") instead
of a stream of per-file warnings.

## Interrupted writes

The supervisor kills a run that exceeds its time limit, and a container can stop at any
moment, so a capture can be cut off part-way. What that can leave behind, inside the `dt=`
folders the run was writing:

| Residue | Cause | Effect on readers |
|---|---|---|
| `.tmp_<hex>` files | Interrupted before step 5 | None (dotfiles are skipped); wastes disk |
| A payload without its sidecar | Interrupted between the two links | None in WearStream (not a capture) |

`repair(root, source, days)` cleans up after an interrupted run. `days` is a set of partition
days; repair visits the `dt={day}` folder of every collection of `source` for each day. In each
folder it deletes temporary files, and only temporary files. A lone payload or lone sidecar is
left in place and logged once, by this repair (`capture_orphan_found`): the writer never deletes
a file it can't prove is its own unfinished work.

Repair is targeted, never a whole-tree walk. The supervisor records when each run starts and
finishes; it runs repair over the days of a run it killed, and at startup over the days of any
run that started without finishing, in both cases while holding that source's lock and before
any new run of that source starts. A killed or unfinished run does not count as successful, so
the supervisor runs that unit of work again, and that re-run is what re-fetches the data the
interrupted capture lost.

## Logging

Capture log lines carry `source`, `collection` and `dt`, and repair lines carry `source` and
`dt` per folder. Payload contents, `request_url` and `request_params` are never logged. The `error` field is the
exception type and its message, truncated to 300 characters; validation failures say which
name is invalid and why without echoing the value. A value that must be logged (a path, an
invalid name in a debug context) is escaped, so it can't inject lines into the log.

| Event | Level | When |
|---|---|---|
| `capture_written` | DEBUG | A capture was written; adds `path` and `byte_size` |
| `capture_skipped_identical` | DEBUG | Skip-identical matched an earlier capture |
| `capture_skipped_non_2xx` | WARNING | The status guard refused a payload; adds `http_status` |
| `capture_failed` | WARNING | Any failure; adds `error` and `error_kind` |
| `capture_folder_sync_failed` | WARNING | A written capture's folder `fsync` failed; adds `error` |
| `capture_orphan_found` | WARNING | Repair found a lone payload or sidecar; adds `path` |
| `capture_tmp_removed` | INFO | Repair deleted temporary files; adds `count` |

A single failed capture is a WARNING because the run continues and the next run retries. The
run-level log line decides whether failures need a person.

## Return value

`capture` returns a `CaptureResult` with an `outcome`, a `path` and an `error_kind`:

| `outcome` | When | `path` | `error_kind` |
|---|---|---|---|
| `written` | Both files were linked into place | The payload's path | None |
| `skipped_identical` | Skip-identical matched an earlier capture | None | None |
| `skipped_non_2xx` | The status guard refused the payload | None | None |
| `failed` | Any failure, including a rolled-back sidecar link | None | `environment` or `other` |

A run can then tell "nothing changed" apart from "writes failed" and report per-run counts.

## Obligations on the supervisor

This design relies on the supervisor to: run `self_test` on `/data` before starting any
capture and refuse to capture when it fails; hold one lock per source for every run of that
source, including runs an operator starts by hand; record each run's start and finish in
`/state`; run `repair` as described in *Interrupted writes*; re-run a unit whose run was killed
or did not finish; and stop a run that receives an `environment` failure.

## Non-goals

- No parsing, typing or transformation of payloads (beyond secret screening).
- No deletion, retention or compaction: the tree grows append-only.
- No storage backend other than the local filesystem in v1 (tracked in issue #2).

## Decisions & Alternatives

| Decision | Chosen | Alternatives Considered | Rationale |
|---|---|---|---|
| Layout and sidecar format | Identical to grecohome-dagster bronze, `schema_version` `v1` | A fresh layout (per-source manifests, NDJSON batches, Parquet at capture time) | Tenet: compatibility with existing captures. An existing tree is continued in place, and any reader written for it keeps working. |
| Error responses | Not captured (2xx only when a status is recorded) | Capture non-2xx bodies as diagnostic records | An error envelope stored as a payload looks like data to every reader and to content checks. The source's log explains the gap. |
| Partition folder | The caller's partition day, defaulting to the fetch date | Always the fetch date | Re-captures of a trailing day must land in, and be compared against, that day's folder; that is what makes skip-identical work. Local-day meaning belongs to readers. |
| Writer-set sidecar fields | `processor`, `processor_version`, `stored_encoding`, `schema_version` set by the writer | Caller-set, as grecohome allowed | One product, one value; the writer knows its own version, nothing is compressed at rest, and the schema version describes the writer's output. No source can mislabel them. The sidecar format is unchanged, only who sets the values. |
| Name validation | Reject names outside fixed patterns, and any `ext` that collides with the sidecar suffix | Trust callers, as grecohome did | A validated name is the cheap guarantee that no path escapes the root, a payload can't be mistaken for a sidecar, and every name is a safe object key. |
| Write sequence | Write and fsync both temporary files, link payload, link sidecar, fsync the folder | Write and link the payload before writing the sidecar (grecohome); sidecar first | The supervisor kills runs, so the payload-without-sidecar window matters. Preparing both first narrows it to two syscalls. Sidecar first is rejected: skip-identical could match a sidecar whose payload never landed. |
| Filesystems without hard links | Not supported; `self_test` fails and capture does not start | Fall back to exclusive-create on the final name; `renameat2` with no-replace | Exclusive create on the final name lets readers see a half-written file. `renameat2` is not portable from Python. Requiring links keeps every write atomic and append-only, and fails loudly at startup rather than per file. |
| Lone payloads after a kill | Leave in place, exclude from "capture", log once during repair | Delete them; move them to a quarantine folder | Deleting breaks the rule that the writer removes only what it can prove is its own temporary work, and an adopted tree may hold lone files from other tools. Excluding them by definition makes them harmless. |
| Repair scope | The days of runs that were killed or did not finish, from the supervisor's run records | Walk the whole tree at startup | A whole-tree walk is slow on a large adopted tree and re-reports the same orphans on every start. The run records name exactly the folders an interruption can have touched. |
| Recovering an interrupted capture's data | The supervisor re-runs the unit of work | Rely on backfill to refill the day | Backfill fills only days with no capture, and a trailing-window folder usually already has captures, so backfill would never notice the loss. |
| Capture order and clock steps | Order by `ms`, ties by stem; accept backward clock steps | Force `ms` to increase within a folder | Forcing monotonic times records a fetch time that didn't happen. Backward steps on a time-synchronised host are rare and small, and a tie only decides which of two captures of the same moment is "newest". |
| Request-parameter comparison | grecohome's canonical JSON encoding, fixed | A stricter canonical form | Any other encoding silently stops matching the sidecars already in an adopted tree, re-capturing every multi-request folder once. |
| Secrets in `request_url` and `request_params` | The calling source keeps them out, proven by its tests | The writer screens parameter keys with the secret pattern list | The broad pattern list matches legitimate pagination cursors (Whoop's `nextToken`), and redacting them would lose replay information and break request matching. |
| File permissions | Follow the process umask | Force `0600`/`0700` | Host tools that read `/data` (analysis notebooks, DuckDB) keep working under the operator's chosen user and umask; an operator who wants privacy sets a stricter umask. |
| Environmental write failures | Classified as `environment`; the run stops and reports once | Treat like any failed capture | Disk full, read-only and permission errors repeat on every capture; one actionable error beats a stream of warnings. |
| Failure behavior | Log, report `failed`, never raise an ordinary error | Raise to the caller | A failed write must not abort the rest of a run; the outcome counts make it visible. Process interrupts still propagate so a run can be stopped. |
| Secret screening location | In this component, applied by sources per endpoint | In each source (grecohome kept it in Garmin) | Tenet: shared core. Any source that reserializes JSON can need it, and one list of patterns is easier to keep broad. |

## Open Questions & Future Decisions

### Deferred
1. Object-store backend: exclusive create maps to put-if-absent; atomic pairing and folder
   listing need restating (issue #2).

## References

- `docs/high-level-design.md`: "The capture tree is the contract", "Partitions are UTC fetch
  slices"
- grecohome-dagster `docs/intent/core/bronze/bronze-design.md`: the bronze writer this
  component continues
