---
parent: high-level-design
prefix: WHOOP
---

# Whoop source

## Context and Design Philosophy

The Whoop source keeps a person's Whoop data captured under `/data/whoop/`: four daily range
collections (`sleep`, `recovery`, `workout`, `cycle`) and two current-only snapshots
(`profile`, `body_measurement`). Whoop rescores and deletes records after the fact, so recent
days are re-captured every hour across a trailing window and stored only when they change.

The hard part of this source is **authentication**, not capture. Whoop uses OAuth 2.0 with a
**single-use, rotating refresh token**:

- Every refresh consumes the current refresh token `R` and returns a new one, `R'`.
- Presenting a consumed `R` a second time trips Whoop's **reuse detection**, which revokes the
  whole grant.
- A revoked grant can't be recovered in code. Only a person, authorizing again in a browser,
  restores it.

A lost grant stops all Whoop capture until the operator notices and runs `wearstream auth
whoop`. So the design leans hard towards **never presenting a refresh token twice** (the HLD's
first tenet): when that rule conflicts with prompt capture, it wins. A refresh that might have
reached Whoop is never retried, and a refresh in flight is never cut off by a stop signal.

## What the operator provides

Whoop issues API credentials per developer application, so each person registers their own
application in the Whoop developer dashboard:

- **Scopes:** `read:sleep`, `read:workout`, `read:recovery`, `read:cycles`, `read:profile`,
  `read:body_measurement` and `offline` (the last is what makes Whoop issue refresh tokens).
- **Redirect URI:** any URI the operator chooses, registered exactly as WearStream is
  configured with it. The default is `http://localhost:8000/callback`. Nothing listens there:
  the browser lands on a page that fails to load, and the operator copies its address into
  `wearstream auth whoop` (see the auth design).
- **Client id and client secret**, supplied to WearStream as secret files.

## Components

| Component | Doc | Spec prefix | Owns |
|---|---|---|---|
| Token lifecycle | [auth/auth-design.md](auth/auth-design.md) | `WHOOP-AUTH` | The `wearstream auth whoop` bootstrap, token storage, refresh, serialization and retry rule, the credential fingerprint, the credential-exchange critical section, credential-safe logging, the token-health check |
| Capture | [capture/capture-design.md](capture/capture-design.md) | `WHOOP-CAPTURE` | The job declarations, range and snapshot capture, data-request retry and rate limit, pagination, the collection check settings |

The two meet at one point: the API client obtains its bearer token from the token manager
before every data request. A failure there is a non-retryable `WhoopAuthError`, which the
data-request retry never retries.

## How the source meets the supervisor's obligations

| Supervisor obligation | Where it is met |
|---|---|
| Declare jobs | Capture: `range`, `snapshots`, `checks` |
| Report a needed credential (child exits `4`) | Auth: missing or unreadable token or client files, an unwritable `/secrets/whoop`, a refresh refused with 400 or 401 (classified by OAuth error), or a data request answered with 403 |
| Decide which responses are failed requests | Capture: a data request that fails after its retries, or a token failure that isn't a needed credential |
| Credential fingerprint without a network call | Auth: a sha256 over the token file's bytes, mode and owner and both client files, taken when the credential is found unusable and reported in the result |
| Mark each credential exchange | Auth: each refresh attempt and the write of its result, and the authorization-code exchange and its write |
| Keep every exchange within 40 seconds | Auth: a 30-second wall-clock deadline per attempt plus a write limited to 5 seconds; no exchange starts with a stop pending |
| Capture a `partitioned` unit with its day as `dt` | Capture: every range page is captured with the unit's day |

## References

- `docs/high-level-design.md`: tenet "Protect the credential over fresh data"; "Whoop refresh
  tokens are single-use"
- `docs/intent/supervisor/supervisor-design.md`: jobs, exit codes, "Obligations on other
  components"
- grecohome-dagster `docs/intent/whoop/`: the token lifecycle and capture this source continues,
  and the incident history behind its rules
