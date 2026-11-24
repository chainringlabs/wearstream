---
parent: whoop
prefix: WHOOP-AUTH
---

# Whoop token lifecycle

## Context

Whoop's single-use, rotating refresh token (see the parent [Whoop design](../whoop-design.md))
makes the token the one piece of state whose loss stops all Whoop capture. This component gets
the first token through `wearstream auth whoop`, keeps a valid access token available to the
API client while **never presenting a refresh token twice**, tells the supervisor when a
person is needed, and keeps every credential out of the logs.

## Storage

| File | Holds | Written by |
|---|---|---|
| `/secrets/whoop/client_id`, `/secrets/whoop/client_secret` | The application's credentials, one value per file; surrounding whitespace is ignored | The operator (read-only to WearStream) |
| `/secrets/whoop/token.json` | `access_token`, `refresh_token`, `token_type`, `expires_at` (ISO 8601 UTC), `scopes`, `user_id`, `refreshed_at` and `refresh_failed_at` (ISO 8601 UTC, see *When to refresh*) | `wearstream auth whoop` and every refresh |
| `/secrets/whoop/token.json.new` | A token being bootstrapped, before it is confirmed | `wearstream auth whoop` |
| `/secrets/whoop/token.json.lock` | Nothing; the target of the token lock | Created on first use |

`token.json` requires `access_token`, `refresh_token` and `expires_at`; any other key it holds is
kept when the file is rewritten. It is plaintext JSON, rewritten whole and atomically on every
change (a temporary file in the same folder, created with mode `0600` and fsynced, renamed into
place, then the folder fsynced), so a reader never sees half a token and a crash mid-write can't
lose a rotated refresh token. There's no application-level encryption: the `/secrets` mount and
the host protect it.

A token file copied from another tool is not adopted. Two processes refreshing one grant revoke
it, so the operator runs `wearstream auth whoop` instead.

## Getting the first token: `wearstream auth whoop`

The operator runs it interactively, in the running container (`docker exec -it`) or a one-off
container with the same mounts. It never listens on a port. It refuses to start unless
`/state/locks` exists, because without the shared lock folder it could not exclude a running
Whoop unit. In order, it:

1. reads the client files, and prints the authorization URL: client id, redirect URI, the
   required scopes, a random `state` and a PKCE challenge (S256);
2. asks the operator to authorize in a browser and paste the full address the browser was
   redirected to (an authorization code expires within minutes, so the prompt says to paste it
   promptly);
3. parses the pasted text (see below) and checks that `state` matches;
4. takes the Whoop source lock (`/state/locks/whoop.lock`) with a blocking `flock` that gives up
   after 10 minutes, saying it is waiting for a running Whoop unit; if it gives up, it exits
   without exchanging the code;
5. exchanges the code for tokens and writes the result to the staging file `token.json.new`, as
   one credential exchange (below), under the token lock;
6. refuses the result if it lacks a refresh token or any required scope, naming what is missing,
   deleting the staging file and leaving `token.json` untouched;
7. fetches the Whoop profile, and compares its user id with the `user_id` in any existing
   `token.json`: a different user is refused the same way, unless the operator passed
   `--new-account`;
8. adds the `user_id` to the staging file and renames it over `token.json` atomically, still
   under the token lock;
9. releases the locks and prints the Whoop user id and the granted scopes, never a token.

Writing to a staging file first keeps the credential exchange itself short (the token request
and one write), while the checks in steps 6 and 7 can take their time. If the command is stopped
during those checks, `token.json` is untouched, and the operator simply runs it again; a later
run overwrites any leftover staging file. A stop arriving during step 5 is held until the
exchange ends, like any credential exchange.

The source lock is held only for steps 4 to 9, so a running daemon is never blocked while the
operator is in the browser. Holding it for the exchange and write means no unit can be
refreshing the old grant while the new token is written; otherwise a refresh on the old grant
could fail after the new token is in place.

**Parsing the pasted text.** Surrounding whitespace and quotes are removed. The text must be a
URL with a query string; a fragment is ignored and query values are URL-decoded. An `error`
parameter (`access_denied` when the operator declined) ends the command with that error and no
exchange. A missing `code` or `state`, or a `state` that doesn't match, ends the command without
an exchange. A bare code is refused, because its `state` can't be checked. If the browser never
reached the redirect URI at all, the redirect URI configured in WearStream doesn't match the one
registered with Whoop, and the command's help text says so.

**Exit codes.** `0` success; `1` the exchange, the scope check, the profile request or the write
failed; `2` invalid input (a pasted text that can't be used, a declined authorization, a state
mismatch, a different account without `--new-account`); `5` the source lock could not be taken.
If the profile request in step 7 fails, nothing is written and the command exits `1`: the
account can't be confirmed.

## When to refresh

Refreshes happen at minute 17, the minute the scheduled capture jobs fire, because refreshing at
the top of the hour is what lost grants (see the capture design). Which unit may refresh depends
on its trigger:

- **A `schedule` unit** refreshes when the stored token was last refreshed (`refreshed_at`) more
  than 30 minutes ago or is within 5 minutes of `expires_at`. The first unit of each tick
  refreshes; the other units of the tick find a fresh token and reuse it.
- **A unit with any other trigger** (`catchup`, `retry`, `backfill`, `manual`) uses the stored
  token while it has not expired, and refreshes only once it has expired. It never starts a
  refresh in minutes 55 to 59 or 0 to 5 of a UTC hour; holding an expired token in that window,
  it reports a failed request (`whoop_refresh_deferred`, WARNING) and the next scheduled tick
  refreshes.
- **After an uncertain failure** (any refresh failure in which Whoop may have received `R`: an
  HTTP error other than a needed credential, a read or write timeout, the attempt deadline, a
  mid-flight error), the token manager records `refresh_failed_at` in `token.json`. Until a
  `schedule` unit refreshes successfully, no other unit refreshes, so `R` is not presented again
  within seconds by the next unit. A successful refresh clears `refresh_failed_at`.

Without these rules, units that run between ticks (a backfill pass runs one every minute or so
for hours) would refresh a few minutes before expiry each hour, walking the refresh minute back
until it reached the top of the hour. With them, refreshes return to minute 17 at the first
scheduled tick after any off-schedule refresh (after downtime, say).

## Serializing the refresh

Only one process works on the Whoop source at a time (the supervisor's source lock), but a
refresh is guarded on its own as well, because `wearstream auth whoop` and a hand-started run
must never interleave with it:

1. an `asyncio.Lock` serializes callers inside one process;
2. an exclusive `flock` on `token.json.lock` serializes processes; the kernel releases it if
   the holder dies.

After acquiring each lock the caller **re-reads the token file** and applies the rules in *When
to refresh* to what it read. If another caller has already refreshed it, the waiter uses that
token. Otherwise it refreshes with the refresh token it just re-read, never with a copy held from
before the lock.

## The refresh request

- A `POST` to `https://api.prod.whoop.com/oauth/oauth2/token` with
  `grant_type=refresh_token`, `client_id`, `client_secret`, the refresh token and
  **`scope=offline`**, which Whoop's refresh flow uses to keep issuing a rotated refresh token.
- Each attempt uses a new connection, never a pooled one shared with data requests, so a stale
  keep-alive connection can't turn into an ambiguous mid-flight error.
- Each attempt has a **30-second wall-clock deadline covering connect, send and receive**, with
  connect alone limited to 5 seconds. Whoop's token endpoint routinely takes 1–4 seconds; a
  short read timeout fires after Whoop has already rotated `R`, which loses `R'`.

## Before presenting a refresh token

Immediately before each attempt, the refresher creates and fsyncs a temporary file in
`/secrets/whoop/`, and removes it. If that fails (a read-only mount, a full disk, wrong
ownership), the refresh is not attempted, so Whoop never rotates a token that could not then be
saved. The failure needs a person (see *When a person is needed*).

## Credential exchanges

A **credential exchange** is one request to the token endpoint (a refresh attempt or the
authorization-code exchange) together with the atomic write of its result (for the
authorization-code exchange, the write of the staging file). While a process is
inside one, it defers `SIGTERM` and `SIGINT` until the exchange ends (the supervisor's
contract), because a stop between Whoop rotating `R` and `R'` being written loses the grant. A
process never **starts** an exchange while a stop signal is pending; it stops instead. A stop
signal is never turned into a token error: it is neither wrapped as a `WhoopAuthError`, nor
retried, nor reported as a failed request or a needed credential. Each
exchange is bounded by the 30-second attempt deadline plus a write limited to 5 seconds, inside
the supervisor's 40-second budget. The time between retry attempts is outside any exchange,
since an attempt that failed to connect never presented `R`.

## Retry rule

A refresh is retried (up to 3 attempts, exponential backoff from 1 to 8 seconds) **only on a
connect-phase transport error**: `ConnectError`, `ConnectTimeout` or `PoolTimeout`. Then the
request provably never reached Whoop, so `R` was never presented.

That is the **only** retry a refresh ever gets. The API client obtains its bearer token inside
its own data-request retry, which retries timeouts and network errors on data requests; so any
failure to obtain a token reaches the API client as a **non-retryable** `WhoopAuthError`.
Otherwise a refresh that timed out after Whoop rotated `R` would be re-run by the data-request
retry and replay `R`.

Raised without retry, because Whoop may have received `R`: any HTTP response (4xx or 5xx), a
read or write timeout, the attempt deadline, and any other mid-flight error. A transient 5xx
fails one unit; the next tick refreshes with `R` intact if Whoop did not consume it.

## Handling the response

- The response must contain `access_token` and an `expires_in` that is a positive integer;
  otherwise it is malformed and the refresh raises. If a malformed 200 still carries a rotated
  `refresh_token`, that `R'` is written first, with `expires_at` set to now, so the next caller
  refreshes with `R'` instead of replaying the consumed `R`. A malformed 200 without `R'` leaves
  the file untouched.
- The rotated `refresh_token` replaces the stored one, and `expires_at` becomes now plus
  `expires_in`.
- A 200 without a rotated refresh token keeps the current one and logs
  `whoop_token_no_rotation` at WARNING. It shouldn't happen, and making it loud means a silent
  reuse of a consumed token can't hide.

**A data request answered with 401.** The access token was refused even though it looked valid
(clock skew, or Whoop revoked it early). The token manager sets the stored `expires_at` to now,
so a later unit refreshes under the rules above, and the data request fails without retry.

## When a person is needed

The token manager distinguishes failures a person must fix from transient ones. The first kind
is reported so the child exits `4` and the supervisor puts the source into `waiting_for_auth`;
once it waits, no refresh is attempted, so a dead grant is never presented to Whoop again.

| Condition | Event (ERROR) | Action |
|---|---|---|
| No token file | `whoop_credential_missing` | Run `wearstream auth whoop` |
| No client id or secret file, or one is empty | `whoop_client_credentials_missing` | Supply `/secrets/whoop/client_id` and `client_secret` |
| Token file unreadable (permissions, invalid JSON, a required key missing) | `whoop_token_unreadable` | Fix the file's permissions, or run `wearstream auth whoop` |
| `/secrets/whoop` not writable (the pre-check, or a failed write of a rotated token) | `whoop_token_folder_unwritable`, with the errno | Make `/secrets/whoop` writable by the container user (or free space); WearStream resumes on its own. If the failure was the write of a rotated token, the grant is lost: run `wearstream auth whoop` |
| Refresh refused with OAuth error `invalid_grant` | `whoop_token_invalid_grant` | Run `wearstream auth whoop` |
| Refresh refused with OAuth error `invalid_client`, or `unauthorized_client` | `whoop_client_rejected` | Check the client id and secret files against the Whoop developer dashboard |
| Refresh refused with any other 400 or 401 | `whoop_token_refused`, with the status and OAuth error code | Run `wearstream auth whoop` |
| A data request answered with 403 | `whoop_scope_missing` | Run `wearstream auth whoop` and grant every `read:` scope |

The OAuth `error` code is read from the token endpoint's JSON response; nothing else from the
body is read or logged. Any other refresh failure (a 5xx, a 429, a timeout, the attempt
deadline, connect errors after three attempts) is **not** a needed credential: the unit fails
(`whoop_token_refresh_failed`, WARNING) and the next tick tries again.

**Credential fingerprint.** For the supervisor's resume check, the fingerprint is the sha256 of
the concatenation of, for the token file and both client files: whether it exists, and its
size, modification time (nanoseconds), inode, mode and owner; then the mode and owner of
`/secrets/whoop`, and whether a temporary file can be created there right now. It uses file
metadata only, never file contents, so nothing derived from a secret reaches `/state`, which
may be shared in a bug report. It is
computed from the files alone, without a network call. The child takes it at the moment it finds
the credential unusable and reports it in its result; the supervisor recomputes it once a minute
while the source waits. A new token, a corrected client file, or a fixed or freed-up folder changes
it; a dead token left in place does not.

## Token health check

`whoop_token_health` runs with the Whoop checks job and reads only the token file, never Whoop.
It fails at ERROR (action: run `wearstream auth whoop`) when `expires_at` is more than 90 minutes
in the past, or the file is missing or unreadable. With hourly refreshes, a token that old means
refreshes have stopped succeeding or stopped happening. While the source is waiting for a
credential, in a rate-limit cooldown, or capture is paused, no refresh can happen and the
supervisor is already telling the operator why, so the check reports `suppressed` with the
reason instead of failing, under the checks component's suppression rule (which also covers the
time until the first successful unit after the source resumes).

## Credential-safe logging

These paths hold credentials in local variables: the authorization-code exchange (client
secret, authorization code, PKCE verifier), the refresh (client secret, refresh token) and the
API request (bearer token). On them:

- **Expected failures** (an HTTP error status) log the event, `status_code`, and for the token
  endpoint the OAuth `error` code.
- **Unexpected failures** log the event and the **exception type name only**: no traceback, no
  exception text, because exception text can embed a header value. A re-raised exception carries
  only the original's type name in its message and drops the original from its cause chain
  (`raise … from None`), so no later traceback can render it.
- **Response bodies of the token endpoint and of API errors are never logged.** A token response
  body is the credential.
- **No part of a token, client secret, authorization code or PKCE verifier is logged or
  printed**, not even a prefix. The client id, the OAuth `state` and the PKCE challenge are not
  secrets and may be shown.

This is one of two layers: WearStream's shared logger also renders every traceback without local
variables (operator-interface design), as the backstop for a path nobody thought of.

## Decisions & Alternatives

| Decision | Chosen | Alternatives Considered | Rationale |
|---|---|---|---|
| Token storage | Plaintext JSON under `/secrets/whoop/`, rewritten atomically | Encrypted at rest with an application key | The refresh token rotates every refresh, so storage must be writable and crash-safe. Encryption at rest belongs to the mount and host, and an application key would be one more secret to manage. |
| Refresh retry policy | Retry connect-phase transport errors only | Retry 5xx and 429 with backoff; retry all transport errors | Retrying anything that may have reached Whoop replays `R` and revokes the grant; a 5xx retry and a read-timeout retry each killed a grant in grecohome-dagster. The cost is that a transient 5xx fails one unit. |
| Refresh deadline | 30 seconds wall clock per attempt, 5 to connect, fresh connection | Per-phase HTTP timeouts; a pooled connection | Per-phase timeouts don't bound an attempt, so one could outlast the supervisor's grace and be killed mid-rotation. A stale pooled connection fails ambiguously. |
| Stop signals during an exchange | Deferred until the exchange ends; no exchange starts with a stop pending | Let the signal stop the process | A stop between rotation and the write loses the grant (tenet: protect the credential). |
| Who may refresh, and when | `schedule` units on their tick; other units only with an expired token, never in minutes 55–05; nobody but a `schedule` unit after an uncertain failure | Any unit within 5 minutes of expiry | Off-schedule units would walk the refresh minute back to the top of the hour, where grants were lost, and would re-present `R` seconds after an uncertain failure. |
| Bootstrap write | Stage the exchanged token, verify, then rename into place | Verify before writing; write directly | The exchange must fit the 40-second budget, and the verification can't; staging keeps the exchange short without risking `token.json`. |
| Writability before a refresh | Pre-check `/secrets/whoop` before presenting `R` | Discover a failed write afterwards | Afterwards is too late: Whoop has rotated `R` and the grant is lost. |
| Cross-process serialization | `flock` on `token.json.lock` plus a re-read, in addition to the source lock | Rely on the source lock alone | The bootstrap command and hand-started runs touch the token too; a dedicated lock keeps the refresh rule independent of how processes are scheduled. |
| `scope=offline` on refresh | Sent | Scope only at authorization | Matches Whoop's documented refresh flow; omitting it risks a 200 without a rotated refresh token. |
| Missing rotation in a 200 | Keep the current refresh token and warn | Fail the refresh | Failing would drop a possibly valid grant; the warning makes the anomaly visible. |
| Malformed 200 that still carries `R'` | Write `R'` with an expired `expires_at`, then raise | Discard the response | `R` is already consumed, so discarding `R'` guarantees the next refresh replays `R`. |
| Classifying a refused refresh | By the OAuth `error` code | Any 400 or 401 means a dead grant | A wrong client secret (`invalid_client`) needs a different fix from a dead grant, and re-authorizing can't fix it. |
| Bootstrap lock scope | The source lock only for exchange, check, write and verify | Hold it from the start | The operator's time in the browser is unbounded; holding the lock through it would stall every Whoop unit. |
| Bootstrap flow | Print the URL, paste back the redirected address | A callback server | Tenet: small attack surface. A callback server opens a port, and on a remote host still needs a tunnel. |
| Grants missing scopes or a refresh token | Refused at bootstrap | Accept and fail later | Without `offline` the token dies within an hour; without a `read:` scope every tick fails with an unhelpful error. |
| A different Whoop account | Refused unless `--new-account` | Accept silently | One instance serves one person; a second account would mix two people's data in one tree. |
| Adopting another tool's token | Not supported; re-authorize | Copy the file across | Two refreshers on one grant revoke it, and the operator can't easily prove the other tool has stopped. |
| Dead-grant handling | Report a needed credential; the supervisor stops all refreshes until the fingerprint changes | Keep refreshing every tick | Presenting a dead token hourly gains nothing and hammers Whoop's token endpoint. |
| Fingerprint contents | Metadata of the token and client files, plus the folder's state | The token bytes alone; hashes of the file contents | A missing client file or a permission problem is fixed without touching the token, and the source must resume when it is. Hashing contents would put a value derived from secrets into `/state`; an atomic rewrite changes the metadata anyway. |
| Where credential-safe logging is enforced | Both on credential paths and in the shared logger | Only one of them | Either layer alone has failed in practice: a logger setting that was never applied, and a stray traceback that dumped a request body. |

## Open Questions & Future Decisions

### Deferred
1. **Rotation lost to a failed write despite the pre-check.** A disk can fill between the
   pre-check and the write. Then `R'` is lost and the grant dies; the write failure is reported
   as a needed credential.

## References

- grecohome-dagster `docs/intent/whoop/auth/auth-design.md` and its incident history
- `docs/intent/supervisor/supervisor-design.md`: credential exchanges, exit code `4`,
  `waiting_for_auth`, credential fingerprint
