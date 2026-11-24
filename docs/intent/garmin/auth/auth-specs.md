# Garmin authentication — EARS specs

Design: [auth-design.md](auth-design.md). Prefix `GARMIN-AUTH`. Facets: `STORE`, `BOOT`,
`SIGNIN`, `CLASS`, `EXCH`, `GUARD`, `FP`, `HEALTH`, `LOG`.
"The token file" is the library's `token_file_path` for `/secrets/garmin`; "the source lock" is
the supervisor's `/state/locks/garmin.lock`; "a unit" is a Garmin `daily` or `reference` unit.

## Storage and environment

- [x] **GARMIN-AUTH-STORE-001**: The Garmin source shall read the optional email and password from `/secrets/garmin/email` and `/secrets/garmin/password`, ignoring one trailing newline.
- [x] **GARMIN-AUTH-STORE-002**: When any WearStream process (units, `wearstream auth garmin`, the daemon, checks) uses the `garminconnect` library, it shall first remove `GARMINTOKENS` from its own environment, logging `garmin_tokens_env_ignored` at WARNING if it was set.
- [x] **GARMIN-AUTH-STORE-004**: Where `WEARSTREAM_GARMIN_CHINA` is `true`, every Garmin client WearStream builds (units, `wearstream auth garmin`) shall use Garmin's China site.
- [x] **GARMIN-AUTH-STORE-003**: The Garmin source shall locate the token file with the library's `token_file_path` for `/secrets/garmin`.

## Bootstrap (`wearstream auth garmin`)

- [x] **GARMIN-AUTH-BOOT-001**: If `/state/locks` does not exist when `wearstream auth garmin` starts, then the command shall exit with code 2 and a message saying `/state` must be mounted, without contacting Garmin.
- [x] **GARMIN-AUTH-BOOT-002**: When `wearstream auth garmin` starts, it shall use the email and password files when present and otherwise prompt for whichever is absent, reading the password without echo, and shall never write a prompted value anywhere.
- [x] **GARMIN-AUTH-BOOT-003**: When `wearstream auth garmin` signs in, it shall sign in with the credentials through the library without a token store, so that nothing is written to `/secrets/garmin` during the sign-in.
- [x] **GARMIN-AUTH-BOOT-004**: When Garmin asks `wearstream auth garmin` for an MFA code, the command shall prompt for it and, if no code is entered within 5 minutes, exit with code 2 without writing anything.
- [x] **GARMIN-AUTH-BOOT-005**: If the session from `wearstream auth garmin`'s sign-in lacks a non-empty `di_token` or `di_refresh_token`, then the command shall exit with code 1 without writing anything.
- [x] **GARMIN-AUTH-BOOT-006**: If the signed-in profile id differs from the one in `/secrets/garmin/account.json` and the operator did not pass `--new-account`, then `wearstream auth garmin` shall exit with code 2, naming both profile ids, without writing anything.
- [x] **GARMIN-AUTH-BOOT-007**: When `wearstream auth garmin`'s sign-in and checks succeed, the command shall take the source lock with a blocking `flock` that gives up after the longest effective time limit of Garmin's capture jobs plus 2 minutes (exiting with code 5 if it does), write the session (`dumps()`) to the token file and the profile id to `/secrets/garmin/account.json`, each atomically through a temporary file created with mode `0600`, release the lock, print the display name and profile id, and exit with code 0.
- [x] **GARMIN-AUTH-BOOT-008**: While `wearstream auth garmin` waits for the operator (credentials or an MFA code), it shall not hold the source lock.
- [x] **GARMIN-AUTH-BOOT-009**: If `wearstream auth garmin`'s sign-in fails for a reason other than those in GARMIN-AUTH-BOOT-004 to BOOT-006, then the command shall exit with code 1 without writing anything.

## Unit sign-in

- [x] **GARMIN-AUTH-SIGNIN-001**: When a unit starts, it shall resume from the token store with a client built without the email and password, and every call in the unit shall use the client that signed in.
- [x] **GARMIN-AUTH-SIGNIN-006**: When a unit's resume finds the token file missing or unloadable, or is refused with HTTP 401, the unit shall treat the store as holding no usable session and, subject to GARMIN-AUTH-SIGNIN-003 and GARMIN-AUTH-CLASS-003, do a password sign-in on a fresh client built with the email and password; any other resume failure shall be classified by GARMIN-AUTH-CLASS-* without a password sign-in.
- [x] **GARMIN-AUTH-SIGNIN-007**: If a stop signal is pending when a unit's sign-in fails, then the unit shall end as stopped without classifying the failure.
- [x] **GARMIN-AUTH-SIGNIN-002**: A unit shall give the library an MFA callback that records that Garmin asked for a code and then raises, and shall never prompt.
- [x] **GARMIN-AUTH-SIGNIN-003**: Before a unit does a password sign-in, it shall read and update `/state/garmin/signin.json` under `/state/locks/state.lock`: if it records a password sign-in less than an hour earlier, the unit shall not sign in and shall report that Garmin is rate-limiting it, with the time remaining in that hour, rounded up to whole seconds and clamped to between 1 and 3600, as the wait; otherwise it shall record the current time atomically and sign in.
- [x] **GARMIN-AUTH-SIGNIN-008**: If a unit cannot read or record `/state/garmin/signin.json` (the lock wait times out or the write fails), then it shall not do a password sign-in and shall report a failed request.
- [x] **GARMIN-AUTH-SIGNIN-004**: If a unit's resume fails in a way GARMIN-AUTH-CLASS-005 classifies as a failed request, then the unit shall retry the resume with a fresh client without credentials, up to 3 attempts in total with backoff from 2 to 15 seconds; the unit shall never retry a password sign-in.
- [x] **GARMIN-AUTH-SIGNIN-005**: If a call fails with `GarminConnectAuthenticationError` after a unit has signed in, then the unit shall make no further calls and report a failed request (`garmin_signin_failed`).

## Classifying a failed sign-in

- [x] **GARMIN-AUTH-CLASS-001**: If Garmin asked a unit's MFA callback for a code, then the unit shall report a needed credential and log `garmin_mfa_required` at ERROR with the action to run `wearstream auth garmin`, whatever exception the library raised, without retrying.
- [x] **GARMIN-AUTH-CLASS-002**: If a unit's password sign-in is rejected by Garmin's SSO as invalid credentials (by the sign-in step that failed and the library's message), then the unit shall report a needed credential and log `garmin_signin_refused` at ERROR with the action to check the email and password and run `wearstream auth garmin`, without retrying.
- [x] **GARMIN-AUTH-CLASS-003**: If a unit finds no usable session and the email file, the password file or both are missing, then it shall report a needed credential and log `garmin_credentials_missing` at ERROR with the action to run `wearstream auth garmin` or supply the credential files.
- [x] **GARMIN-AUTH-CLASS-004**: If a unit's failure is a `GarminConnectTooManyRequestsError`, carries an HTTP 429 or 427 anywhere in its cause or context chain, or follows a session refresh the exchange wrapper saw answered 429, then the unit shall make no further calls, report that Garmin is rate-limiting it, and log `garmin_rate_limited` at WARNING.
- [x] **GARMIN-AUTH-CLASS-005**: If a unit's sign-in fails in any way not covered by GARMIN-AUTH-CLASS-001 to CLASS-004 (including a `GarminConnectAuthenticationError` whose cause cannot be placed), then the unit shall treat it as a failed request and log `garmin_signin_failed` at WARNING.

## Session refresh

- [x] **GARMIN-AUTH-EXCH-001**: The Garmin source shall wrap the library's `_refresh_session` so that each call is one credential exchange, during which a received `SIGTERM` or `SIGINT` is held until the call has returned, and shall record when the refresh it made was answered HTTP 429.
- [x] **GARMIN-AUTH-EXCH-002**: If a stop signal is pending, then the Garmin source shall not start a session refresh.

## Token-store guards

- [x] **GARMIN-AUTH-GUARD-001**: Before a unit's sign-in, the unit shall log `garmin_tokenstore_unusable` at ERROR, with the path, a reason and an action, when the library refuses the store's path (`path_rejected`), the token folder exists but isn't a writable, searchable folder (`not_writable`), the folder is missing and its nearest existing parent isn't writable (`parent_not_writable`), or no folder above the token file other than `/` is a mount point (`off_volume`), and shall then sign in.
- [x] **GARMIN-AUTH-GUARD-002**: When a unit whose sign-in succeeded ends, it shall log `garmin_tokenstore_not_saved` at ERROR with the path and the action to make `/secrets/garmin` writable and mounted, unless the token file exists, holds non-empty `di_token` and `di_refresh_token`, and either equals the client's in-memory session or was modified at or after the unit started.
- [x] **GARMIN-AUTH-GUARD-003**: The Garmin source shall never log or report any part of the token file's contents.

## Credential fingerprint

- [x] **GARMIN-AUTH-FP-001**: The Garmin source's credential fingerprint shall be the sha256 of, for the token file and the email and password files, whether each exists and its size, modification time in nanoseconds, inode, mode and owner, followed by the token folder's mode and owner and whether a file can be created there at that moment, computed without a network call and without reading any file's contents, using the literal path `/secrets/garmin/garmin_tokens.json` without following links when the library refuses to resolve the token path.
- [x] **GARMIN-AUTH-FP-002**: When a unit reports a needed credential, it shall take the credential fingerprint at that moment and include it in the unit's result.

## Token-store health check

- [x] **GARMIN-AUTH-HEALTH-001**: When `garmin_tokenstore_health` runs, it shall fail at ERROR with the path, the reason and an action if the store meets any GARMIN-AUTH-GUARD-001 condition, whatever the source's state.
- [x] **GARMIN-AUTH-HEALTH-003**: `garmin_tokenstore_health` shall return its result as a source's own check (CHECKS-RUN-007).
- [x] **GARMIN-AUTH-HEALTH-002**: When `garmin_tokenstore_health` runs and the token file is missing, unreadable, or not JSON holding non-empty `di_token`, `di_refresh_token` and `di_client_id`, it shall read the source's state from `/state/sources/garmin.json` (a missing document meaning active) and capture's state from `/state/heartbeat.json`, and report `suppressed` with the reason while the checks component's suppression rule applies to the Garmin source, and otherwise fail at ERROR with the path, the reason and an action.

## Credential-safe logging

- [x] **GARMIN-AUTH-LOG-001**: The Garmin source shall never log or print any part of the password, an MFA code, or a session token.
- [x] **GARMIN-AUTH-LOG-002**: If a Garmin sign-in or call fails, then the Garmin source shall log only the event and the exception's type name, with no exception text and no traceback, and a re-raised exception shall carry only the original's type name and suppress the cause chain.
- [x] **GARMIN-AUTH-LOG-003**: Unless the operator has enabled library debug logging, every WearStream process that uses the `garminconnect` library (units, `wearstream auth garmin`, the daemon computing fingerprints, checks) shall set the `garminconnect` loggers to emit nothing and not to propagate.
