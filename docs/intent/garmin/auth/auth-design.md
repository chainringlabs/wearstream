---
parent: garmin
prefix: GARMIN-AUTH
---

# Garmin authentication

## Context

Every Garmin unit signs in before it can capture anything. Signing in by **resuming** the saved
session is cheap: a token load, a refresh if the session is near expiry, and the profile and
settings calls every sign-in makes. A **password sign-in** goes through Garmin's consumer SSO,
which rate-limits (429) and Cloudflare-blocks bursts, and may ask for an MFA code that only a
person can supply. This component keeps sign-ins on the cheap path, keeps password sign-ins
rare, and hands the operator a clear action when a person is needed.

Sign-in is delegated to `garminconnect`. WearStream never reimplements Garmin's login, MFA or
session refresh, and uses the library's own function (`token_file_path`) to locate the session
file, so it can never drift from where the library loads and saves.

## Storage

| Path | Holds | Written by |
|---|---|---|
| `/secrets/garmin/` | The token store folder, mode `0700` | WearStream and the library |
| `/secrets/garmin/garmin_tokens.json` | The session (`di_token`, `di_refresh_token`, `di_client_id`, …), mode `0600` | `wearstream auth garmin`, and the library on session refresh |
| `/secrets/garmin/email`, `/secrets/garmin/password` | Optional sign-in credentials, one value per file (a trailing newline is ignored) | The operator (read-only to WearStream) |
| `/secrets/garmin/account.json` | The Garmin profile id the session belongs to | `wearstream auth garmin` |
| `/state/garmin/signin.json` | The time of the last password sign-in by a unit | Garmin units |

The library also reads a `GARMINTOKENS` environment variable as a fallback store, which may hold
a session inline. WearStream removes it from its own environment before using the library, and
logs `garmin_tokens_env_ignored` (WARNING) if it was set, so a leftover setting from another tool
can neither redirect the store nor carry a session in the environment.

A token store copied from, or shared with, another tool that is still running is not supported:
two processes refreshing one session can invalidate it. The operator runs `wearstream auth
garmin` instead.

## Getting a session: `wearstream auth garmin`

The operator runs it interactively, in the running container (`docker exec -it`) or a one-off
container with the same mounts. It refuses to start unless `/state/locks` exists. In order, it:

1. reads the email and password files, or prompts for whichever is absent (the password without
   echo); a prompted value is used for this sign-in only and never written anywhere;
2. signs in with the credentials **in memory**: through the library, with no token store, so
   nothing is written yet and any existing session stays as it is; when Garmin asks for an MFA
   code, the command prompts for it, and gives up after 5 minutes without an answer;
3. checks that the new session holds a non-empty `di_token` and `di_refresh_token` (a sign-in
   that fell back to a web cookie can't be resumed later, so it is refused);
4. compares the signed-in profile id with `account.json`: a different account is refused unless
   the operator passed `--new-account`;
5. takes the Garmin source lock (`/state/locks/garmin.lock`) with a blocking `flock` that gives up
   after the longest effective time limit of Garmin's capture jobs plus 2 minutes (17 minutes by
   default; longer than a unit can hold the lock), saying it is waiting for a running Garmin unit;
6. writes the session (the library's `dumps()`) to the token file atomically (temporary file
   created `0600`, fsync, rename, fsync the folder), and writes `account.json` the same way;
7. releases the lock and prints the account's display name and profile id, never a token or
   password.

Signing in without a token store and writing the file only after the checks means a refused or
abandoned run never replaces the existing session, and a successful run always writes a new
session, so the source's credential fingerprint changes and a waiting source resumes. The source
lock is held only for the write, never during the MFA prompt.

**Exit codes.** `0` success; `1` the sign-in failed or the session can't be resumed; `2` invalid
input (a declined, wrong or timed-out MFA code, a different account without `--new-account`,
`/state` not mounted); `5` the source lock could not be taken.

## Signing in for a unit

Each unit signs in once, and every call in the unit uses the resulting client. Signing in has
two steps, on two separate clients, so the library can never decide on its own to do a password
sign-in:

1. **Resume.** A client built **without** the email and password resumes from the token store.
   If the token file is missing, can't be loaded, or the resume is refused with a 401, the store
   holds **no usable session** and the unit goes on to step 2. Any other resume failure (a 5xx or
   timeout on the profile calls, for example) is a failed sign-in, classified below; the unit does
   not fall back to a password sign-in for it.
2. **Password sign-in**, only when there is no usable session: a fresh client built with the email
   and password signs in, after the hourly limit below allows it. The library saves the new
   session to the store.

- **A pending stop comes first.** If a stop signal is pending when a sign-in fails, the unit ends
  as stopped (the child exits `75`) and the failure is not classified, because an interrupted
  refresh can surface from the library as a missing-credential error.
- **MFA.** A unit never prompts. Its MFA callback records that Garmin asked for a code and then
  raises. The library re-wraps that failure, so the unit checks its own record first: if MFA was
  requested, the sign-in is a needed credential, whatever exception surfaced, and is never
  retried.
- **At most one password sign-in an hour.** Before a password sign-in, a unit reads and updates
  `/state/garmin/signin.json` under `state.lock`. If a unit did a password sign-in less than an
  hour ago, this unit does not sign in: it reports that Garmin is rate-limiting it, with the wait
  until that hour is up (rounded up to whole seconds, at least 1 and at most 3600), and the
  supervisor's cooldown holds the source. Otherwise it records the time and signs in. If it can't
  read or record the time (the lock times out, the write fails), it does not sign in and reports a
  failed request.
  A broken token store then costs one password sign-in an hour rather than one per unit, which
  protects Garmin's SSO from the burst a backfill pass would otherwise cause.
- **Retries.** Only the resume is retried, with a fresh client, up to 3 attempts (backoff 2 to
  15 seconds), and only for failures classified as failed requests. A password sign-in is never
  retried: one SSO sign-in already tries several strategies, and repeating it is what trips
  Garmin's rate limits and risks locking the account.
- **A rejection mid-unit.** If a data call fails with `GarminConnectAuthenticationError` after
  sign-in, the session was refused part-way: the unit makes no further calls and reports a
  sign-in failure (a failed request), and the next unit signs in again.

## Classifying a failed sign-in

The library raises `GarminConnectAuthenticationError` for causes that need a person and for
causes that don't (a profile fetch failing on a 5xx, a transient cookie error). WearStream
classifies by the sign-in step that failed and the library's message, and treats anything it
can't place as transient.

| Condition | Classification | Event | Action |
|---|---|---|---|
| Garmin asked for an MFA code | Needed credential | `garmin_mfa_required` (ERROR) | Run `wearstream auth garmin` |
| Garmin's SSO rejected the email or password | Needed credential, never retried | `garmin_signin_refused` (ERROR) | Check the email and password, then run `wearstream auth garmin` |
| No usable session, and the email file, the password file or both are missing | Needed credential | `garmin_credentials_missing` (ERROR) | Run `wearstream auth garmin`, or supply the credential files |
| Garmin answered 429 or 427 at any step: a `GarminConnectTooManyRequestsError`, an HTTP 429 or 427 anywhere in a failure's cause or context chain, or a session refresh the exchange wrapper saw answered 429 | Rate limited | `garmin_rate_limited` (WARNING) | — |
| A password sign-in within an hour of the last | Rate limited | `garmin_signin_deferred` (WARNING) | — |
| Anything else | Failed request | `garmin_signin_failed` (WARNING) | — |

A needed credential makes the child exit `4`, and the supervisor holds the source in
`waiting_for_auth` until the credential fingerprint changes. A rate limit makes it exit `6`, and
the supervisor holds the source in a cooldown.

## Session refresh as a credential exchange

The library refreshes the session (`_refresh_session`, which sends the refresh through
`_refresh_di_token` and then saves the store) when it is near expiry at sign-in, proactively
before a call, and after a call answered 401; it keeps any rotated refresh token Garmin returns. Whether
Garmin's refresh tokens are single-use is not documented. If they are, a process stopped between
the refresh and the save loses the session. WearStream therefore treats each call of `_refresh_session` as a **credential exchange**: it wraps
that function so a stop signal is held until it has returned (refresh and save both done), and no
refresh starts while a stop is pending. The wrapper also notes when the refresh was answered 429,
because the library swallows refresh errors. The refresh request is bounded by the library's
30-second request timeout, which is a total limit with its `curl_cffi` transport (a delivery
requirement), and the save is local, inside the supervisor's 40-second budget. The rest of a sign-in (the SSO steps of a password
sign-in, the profile calls) is not an exchange: stopping it loses nothing.

## Making a broken store loud

The library fails **silently** when the store can't be used: a failed load is logged only at
debug level and a failed save is swallowed, so a misconfigured store looks healthy while every
unit needs a password sign-in. Each unit checks the store before signing in and after it ends.

**Before the sign-in**, it logs `garmin_tokenstore_unusable` (ERROR, with the path, a reason and
an action) when any of these holds, then signs in anyway:

| Reason | Condition | Action |
|---|---|---|
| `path_rejected` | The library refuses the path (a symlink anywhere in it, for example) | Mount `/secrets/garmin` directly, with no symlinks |
| `not_writable` | The token folder exists but isn't a writable, searchable folder for this process | Make `/secrets/garmin` writable by the container user |
| `parent_not_writable` | The token folder is missing and its nearest existing parent isn't writable | Create `/secrets/garmin` writable by the container user |
| `off_volume` | No folder above the token file (other than `/`) is a mount point, so the session would be lost on the next container replacement | Mount a host folder at `/secrets/garmin` |

**After a unit that signed in successfully**, it logs `garmin_tokenstore_not_saved` (ERROR,
action: make `/secrets/garmin` writable and mounted) unless the token file exists, holds non-empty `di_token` and
`di_refresh_token`, and either holds exactly the session the client has in memory (compared with
`dumps()` in memory, never logged) or was modified at or after the unit started. Comparing
contents, not existence, catches a read-only store that still holds an old file.

## Credential fingerprint

The sha256 of the concatenation of, for the token file and the email and password files: whether
it exists, and its size, modification time (nanoseconds), inode, mode and owner; then the token
folder's mode and owner, and whether a file can be created there at that moment. It reads no
file's contents, so nothing derived from the password or session reaches `/state`, which may be
shared in a bug report. When the library refuses to resolve the token path (a symlink in it), the
fingerprint uses the metadata of the literal default path `/secrets/garmin/garmin_tokens.json`
without following links. The daemon computes the fingerprint with the library too, so it applies
the same `GARMINTOKENS` and logger rules as a unit. The child takes it when it reports a needed credential; the supervisor
recomputes it once a minute while the source waits. A new session from `wearstream auth garmin`,
a new credential file or a fixed folder changes it.

## Token-store health check

`garmin_tokenstore_health` runs with the Garmin checks job and reads only the token store. It
fails at ERROR (with the path, the reason and an action) when the store meets any condition in
the table above, or the token file is missing, unreadable, or not JSON holding non-empty
`di_token`, `di_refresh_token` and `di_client_id`. It never reports any part of the file's
contents, and it doesn't judge how recent the file is: the library rewrites it only on a session
refresh, at an undocumented interval. An unusable store always fails the check, because it is
what causes repeated password sign-ins and cooldowns. Only a missing or invalid token file is
reported as waiting, cooldown or pause instead of a failure while the source is waiting for a
credential, in a rate-limit cooldown, or capture is paused (read from `/state/sources/garmin.json`
and `/state/heartbeat.json`).

## Credential-safe logging

The password, MFA code and session tokens are never logged or printed, not even in part.
Sign-in failures log the event and the exception's type name only, with no exception text and no
traceback, because the library's exception text can embed request details; a re-raised exception
carries only the original's type name and suppresses the cause chain. The library's own loggers
(`garminconnect` and its children) are set to emit nothing and not to propagate, because they log
exceptions with tracebacks and response text; a debug setting re-enables them for
troubleshooting. The shared logger's rule that tracebacks never render local variables
(operator-interface design) is the backstop.

## Decisions & Alternatives

| Decision | Chosen | Alternatives Considered | Rationale |
|---|---|---|---|
| Who owns the session | The library's token store and refresh | A store of WearStream's own; Garmin's Personal API | Only the consumer sign-in reaches the full catalog, and the library handles its Cloudflare, MFA and refresh. The Personal API is tracked in issue #5. |
| Password storage | Optional files; otherwise typed into `wearstream auth garmin` and never stored | Require a password file | Without a stored password a lost session needs a person, but no long-lived password sits on disk. Accounts without MFA can supply the files for unattended re-sign-in. |
| Bootstrap sign-in | In memory, written by WearStream after the checks | Let the library sign in against the store | The library writes the store during sign-in, before an account check could refuse it, and resumes a valid session without signing in at all, so the file and fingerprint wouldn't change. |
| MFA detection | A flag set by WearStream's callback, checked first | Classify the surfacing exception | The library re-wraps the callback's failure into unrelated exception types. |
| Ambiguous `GarminConnectAuthenticationError` | Transient unless the step and message show an SSO credential rejection | Any auth error after retries is a needed credential | The library raises it for 5xx and cookie errors too; misclassifying an outage would park the source until a person acts. |
| Password sign-in frequency | At most one an hour per source; otherwise rate-limited | One per unit; never in backfill | A broken store would otherwise turn every unit, and a backfill pass, into a password sign-in burst; the hourly limit keeps capture going slowly while the store errors are raised. |
| Wrong-password retries | No password sign-in is retried | Retry like other auth errors | One SSO sign-in already tries several strategies; repeats risk Garmin's rate limits and an account lockout. |
| Resume and password sign-in | Two clients: resume without credentials, then a password sign-in only for no usable session | One client given the credentials | Given credentials, the library falls back to a password sign-in on any resume failure, a 5xx included, bypassing the hourly limit. |
| Session refresh | A credential exchange: stop signals held through refresh and save | Not an exchange | Garmin may rotate refresh tokens; holding a stop for at most one 30-second request plus a save costs nothing if it doesn't. |
| Fingerprint contents | File metadata only | Hashes of file contents | A content hash of a weak password could be brute-forced from `/state`; metadata changes on every rewrite anyway. |
| The library's own logs | Silenced unless debugging | Leave them on | They log exceptions with tracebacks and response text, outside WearStream's logging rules. |
| `GARMINTOKENS` | Removed from the environment, with a warning | Honour it | A leftover value could redirect the store or carry a session in the environment. |
| Unusable token store | Log ERROR with an action, then continue | Fail the unit | Failing stops capture over a cost problem; the hourly password sign-in limit bounds the cost. |
| A different Garmin account | Refused unless `--new-account` | Accept silently | One instance serves one person. |
| `garminconnect` version | A lower bound, with the exact version fixed by the lockfile | An exact pin in the manifest | It must follow Garmin's sign-in and API changes; the lockfile keeps builds reproducible. |

## Open Questions & Future Decisions

### Deferred
1. **Does Garmin rotate refresh tokens single-use?** Observe whether the stored
   `di_refresh_token` changes on each refresh. If it never does, the refresh no longer needs to be
   a credential exchange.

## References

- grecohome-dagster [`docs/intent/garmin/auth/auth-design.md`](https://github.com/tgrecojr/grecohome-dagster/blob/0cbe15c78d613ddcda68ce3d60927c36c855912f/docs/intent/garmin/auth/auth-design.md) and the token-store incident it
  records
- `garminconnect` 0.3.x: `Garmin.login`, `client.token_file_path`, `client.dumps`,
  `_refresh_di_token`
