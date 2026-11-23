# Capture — EARS specs

Design: [capture-design.md](capture-design.md). Prefix `CAPTURE`. Facets: `WRITE`, `NAME`,
`SIDECAR`, `READ`, `DEDUPE`, `GUARD`, `FAIL`, `SCREEN`, `TEST`, `REPAIR`, `LOG`.
`capture` is the capture-tree writer; "the read helpers" are the functions that list a folder's
captures and tell whether a folder has data; `repair`, `self_test` and `screen_secrets` are the
functions of those names in this component.

## Writing

- [ ] **CAPTURE-WRITE-001**: When `capture` writes a capture, the system shall write the payload bytes unchanged to `{root}/{source}/{collection}/dt={dt}/{collection}_{fetched_unix_ms}_{short_id}.{ext}` and the sidecar to `{collection}_{fetched_unix_ms}_{short_id}.meta.json` in the same folder, where `fetched_unix_ms` is the UTC fetch time in epoch milliseconds and `short_id` is 6 random lowercase hex characters.
- [ ] **CAPTURE-WRITE-002**: When `capture` is called with a non-empty `dt`, the system shall write into the `dt={dt}` folder; when `dt` is absent or empty, the system shall use the UTC date of the fetch time.
- [ ] **CAPTURE-WRITE-003**: When `capture` is called without an explicit `ext`, the system shall derive it from the caller's `content_type` with parameters dropped, trimmed and lowercased (`application/json` and `text/json` → `json`, `text/csv` and `application/csv` → `csv`, `application/xml` and `text/xml` → `xml`, `text/plain` → `txt`, anything else → `bin`); when an explicit `ext` is given, the system shall use it unchanged.
- [ ] **CAPTURE-WRITE-004**: When `capture` writes a capture, the system shall write the payload and the sidecar to `.tmp_`-prefixed temporary files in the destination folder and fsync both before linking either, then hard-link the payload into place, then the sidecar, then fsync the destination folder.
- [ ] **CAPTURE-WRITE-005**: When `capture` creates a missing folder on the path to a capture, the system shall fsync that folder's parent.
- [ ] **CAPTURE-WRITE-006**: When a `capture` call ends, by returning or by raising, the system shall have removed every temporary file that call created.
- [ ] **CAPTURE-WRITE-007**: If hard-linking a capture's payload or sidecar fails because the name already exists, then `capture` shall remove any file it linked in that attempt, draw a new `short_id` and retry, and shall return outcome `failed` after five attempts.
- [ ] **CAPTURE-WRITE-008**: If hard-linking a capture's sidecar fails for any reason other than the name already existing, then `capture` shall remove the payload it linked in that call and return outcome `failed`.
- [ ] **CAPTURE-WRITE-009**: If fsyncing the destination folder fails after both of a capture's files were linked, then `capture` shall log `capture_folder_sync_failed` and return outcome `written` with the payload's path.
- [ ] **CAPTURE-WRITE-010**: When `capture` has linked both files of a capture, the system shall return outcome `written` with the payload's path.
- [ ] **CAPTURE-WRITE-011**: The `capture` writer shall never replace, modify or delete an existing file in the capture tree, other than temporary files and a payload it linked earlier in the same call.
- [ ] **CAPTURE-WRITE-012**: The `capture` writer shall create files with mode `0644` and folders with mode `0755`, each filtered by the process umask, and shall not change any permission afterwards.
- [ ] **CAPTURE-WRITE-013**: If an ordinary error (a Python `Exception`) occurs during a `capture` call, then the system shall log `capture_failed` and return outcome `failed` without raising.
- [ ] **CAPTURE-WRITE-014**: If a process interrupt (`KeyboardInterrupt` or `SystemExit`) occurs during a `capture` call, then the system shall remove that call's temporary files and let the interrupt propagate.

## Name validation

- [ ] **CAPTURE-NAME-001**: If `capture` is called with a `source` or `collection` that does not match `[a-z0-9_-]+`, a `dt` that is not a real calendar date written `YYYY-MM-DD`, or an `ext` that does not match `[a-z0-9]+(\.[a-z0-9]+)*`, then the system shall write nothing and return outcome `failed`.
- [ ] **CAPTURE-NAME-002**: If `capture` is called with an `ext` equal to `meta.json` or ending in `.meta.json`, then the system shall write nothing and return outcome `failed`.

## Sidecar

- [ ] **CAPTURE-SIDECAR-001**: When `capture` writes a sidecar, the system shall set `source`, `collection`, `fetched_at` (UTC, formatted `YYYY-MM-DDTHH:MM:SS.mmmZ`), `fetched_at_unix_ms`, `byte_size` and `sha256` (both of the stored payload bytes), `stored_encoding` to `identity`, `schema_version` to `v1`, `processor` to `wearstream` and `processor_version` to the installed WearStream version, overriding any caller value for those keys.
- [ ] **CAPTURE-SIDECAR-002**: When `capture` writes a sidecar, the system shall include every caller metadata key not set by the writer (CAPTURE-SIDECAR-001) with its value unchanged.
- [ ] **CAPTURE-SIDECAR-003**: When `capture` writes a sidecar, the system shall serialize it as compact JSON with keys sorted.

## Reading the tree

- [ ] **CAPTURE-READ-001**: The read helpers shall treat as a capture only a stem `{collection}_{ms}_{id}` in a `dt=` folder, with `collection` equal to the folder's collection, `ms` one or more decimal digits, `id` six lowercase hex characters and the name parsed from the right, that has exactly one payload file and exactly one `.meta.json` sidecar, where the sidecar parses as a JSON object whose `sha256` is a string and whose `source` and `collection` equal the folder's.
- [ ] **CAPTURE-READ-002**: The read helpers shall ignore dot-named files, files and folders whose names do not conform to the capture layout, `dt=` folders whose date is not a real calendar date, and subfolders inside a `dt=` folder.
- [ ] **CAPTURE-READ-003**: When the read helpers list a folder's captures, the system shall order them by `ms` newest first, and order captures with equal `ms` by stem with the lexicographically greater stem first.
- [ ] **CAPTURE-READ-004**: When asked whether a `(source, collection, dt)` folder has data, the read helpers shall answer yes if and only if the folder holds at least one capture (as defined in CAPTURE-READ-001).

## Skip-identical

- [ ] **CAPTURE-DEDUPE-001**: Where `dedupe` is true and `dedupe_scope` is `folder` (both the defaults), `capture` shall write nothing when the payload's sha256 equals the `sha256` of the newest capture in the same `(source, collection, dt)` folder, and return outcome `skipped_identical`.
- [ ] **CAPTURE-DEDUPE-002**: Where `dedupe` is true and `dedupe_scope` is `request`, `capture` shall write nothing when the payload's sha256 equals the `sha256` of the newest capture in the same `(source, collection, dt)` folder whose `request_params` equal the call's `request_params`, and return outcome `skipped_identical`.
- [ ] **CAPTURE-DEDUPE-003**: When `capture` compares `request_params` for request-scope skip-identical, the system shall compare their canonical JSON forms: keys sorted, separators `,` and `:` with no spaces, and values JSON cannot encode rendered with `str()`.
- [ ] **CAPTURE-DEDUPE-004**: If `dedupe` is true and the folder holds no capture that the call's `dedupe_scope` selects, then `capture` shall write the payload.
- [ ] **CAPTURE-DEDUPE-005**: Where `dedupe` is false, `capture` shall write every payload that passes the guards, including one identical to an earlier capture.
- [ ] **CAPTURE-DEDUPE-006**: When `capture` performs skip-identical on a folder it has already indexed in the same process, the system shall use that in-process index of the folder's captures, extended with every capture the process has since written there, instead of re-reading the folder.

## Guards

- [ ] **CAPTURE-GUARD-001**: If `capture` receives a payload that is not `bytes` or `bytearray`, then the system shall write nothing and return outcome `failed`.
- [ ] **CAPTURE-GUARD-002**: If the metadata passed to `capture` carries an `http_status` that is not an integer, or is a boolean, then the system shall write nothing and return outcome `failed`.
- [ ] **CAPTURE-GUARD-003**: If the metadata passed to `capture` carries an integer `http_status` outside 200–299, then the system shall write nothing, log `capture_skipped_non_2xx` with the status, and return outcome `skipped_non_2xx`.
- [ ] **CAPTURE-GUARD-004**: The `capture` writer shall apply its checks in this order: name validation (CAPTURE-NAME), payload type, `http_status`, then skip-identical, returning the outcome of the first one that refuses the payload.

## Failure classification

- [ ] **CAPTURE-FAIL-001**: When `capture` returns outcome `failed` because of an operating-system error whose errno is `ENOSPC`, `EDQUOT`, `EROFS`, `EACCES` or `EPERM`, the system shall set the result's `error_kind` to `environment`; for every other `failed` outcome it shall set `error_kind` to `other`; for every other outcome `error_kind` shall be empty.

## Secret screening

- [ ] **CAPTURE-SCREEN-001**: When `screen_secrets` is given a parsed JSON value, the system shall return a copy with every object key removed, at any depth, whose name contains case-insensitively any of `password`, `passwd`, `secret`, `token`, `apikey`, `api_key`, `authorization`, `credential`, `private_key`, `privatekey`, `signature`, `sessionid` or `session_id`, together with the dotted and indexed paths of the removed keys (for example `profile.accessToken`, `devices[0].authToken`).
- [ ] **CAPTURE-SCREEN-002**: The `screen_secrets` function shall not modify the value it is given.

## Self-test

- [ ] **CAPTURE-TEST-001**: When `self_test` runs against a data root, the system shall create a dot-named file in the root, fsync it, hard-link it to a second dot-named name, fsync the root folder and remove both files, and report success only if every step succeeded.
- [ ] **CAPTURE-TEST-002**: If a step of `self_test` fails, then the system shall report which step failed and the operating-system error, and attempt to remove any file the self-test created.

## Repair

- [ ] **CAPTURE-REPAIR-001**: When `repair` runs for a source and a set of partition days, the system shall visit the `dt={day}` folder of every collection folder under that source for each given day, delete the `.tmp_`-prefixed files in it, and log `capture_tmp_removed` with the count for each folder where it deleted any.
- [ ] **CAPTURE-REPAIR-002**: When `repair` finds a lone payload or a lone sidecar in a folder it visits, the system shall leave the file in place and log `capture_orphan_found` with its path.
- [ ] **CAPTURE-REPAIR-003**: The `repair` function shall delete no file other than `.tmp_`-prefixed temporary files, and shall visit no folder outside the given source's collections and the given days.

## Logging

- [ ] **CAPTURE-LOG-001**: When `capture` writes a capture, skips an identical one, or fails, the system shall log `capture_written` (DEBUG, with `path` and `byte_size`), `capture_skipped_identical` (DEBUG) or `capture_failed` (WARNING, with `error` and `error_kind`) respectively, each carrying `source`, `collection` and `dt`.
- [ ] **CAPTURE-LOG-002**: When `capture`, `repair` or `self_test` logs an `error` field, the system shall set it to the exception type and message truncated to 300 characters; a name-validation failure shall name the invalid field and the rule it broke without including the value.
- [ ] **CAPTURE-LOG-003**: The capture component shall never include payload bytes, `request_url` or `request_params` in a log line.
- [ ] **CAPTURE-LOG-004**: When the capture component logs a path or a name, the system shall escape control characters in it.
