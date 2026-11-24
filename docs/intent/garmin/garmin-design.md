---
parent: high-level-design
prefix: GARMIN
---

# Garmin source

## Context and Design Philosophy

The Garmin source keeps a person's Garmin Connect data captured under `/data/garmin/`. It calls
every allowlisted endpoint of the `garminconnect` library's catalog (about 60 collections, plus
ten per-activity endpoints and the original FIT file of each activity) and writes what Garmin
returned through the capture writer. Nothing in it transforms or interprets the data.

Garmin data for a day is **not final when the day ends**. A watch syncs late, Garmin computes
some daily aggregates hours or days later, and summaries are restated as more data arrives.
The second capture of a day differs from the first for most important daily collections
(`user_summary`, `spo2`, `respiration`, `steps_intraday`, `stress`, `body_battery`, `sleep`),
and a device that syncs after a gap back-fills **whole batches of days**, observed reaching at
least a week back. So, following the tenet *Completeness over economy*, every completed day is
re-captured daily across a 14-day trailing window, and skip-identical keeps storage flat.

Garmin Connect has no official API that reaches the full catalog. `garminconnect` signs in
through Garmin's consumer sign-in (handling Cloudflare challenges, MFA and session refresh) and
keeps the session in a token store. WearStream delegates sign-in to it entirely and never
reimplements it. Garmin's official Personal API is tracked as a possible future path (issue #5).

## What the operator provides

- **Garmin Connect email and password**, as secret files, or typed into `wearstream auth garmin`
  (see the auth design for which applies when).
- **An MFA code** when Garmin asks for one, typed into `wearstream auth garmin`.
- **Whether the account is on Garmin's China site** (`garmin.cn`), as configuration.

## Components

| Component | Doc | Spec prefix | Owns |
|---|---|---|---|
| Authentication | [auth/auth-design.md](auth/auth-design.md) | `GARMIN-AUTH` | `wearstream auth garmin`, the per-unit sign-in, the token store and its guards, when a person is needed, the credential fingerprint, the token-store health check |
| Capture | [capture/capture-design.md](capture/capture-design.md) | `GARMIN-CAPTURE` | The job declarations, the catalog and selection, the day unit and the per-activity fan-out, skip-identical per capture path, pacing, the collection check settings |

The two meet at the start of every unit: the unit signs in once (authentication) and every call
in the unit uses that client (capture).

## How the source meets the supervisor's obligations

| Supervisor obligation | Where it is met |
|---|---|
| Declare jobs | Capture: `daily`, `reference`, `checks` |
| Report a needed credential (child exits `4`) | Auth: Garmin asks for MFA, the SSO rejects the email or password, or there is no session and no credentials to sign in with |
| Report a rate limit (child exits `6`) | Auth and capture: a 429 at any step, or a password sign-in within an hour of the last |
| Decide which responses are failed requests | Capture: an endpoint call that raises (after the library's own retries), or the unit stopping early |
| Credential fingerprint without a network call | Auth: a sha256 over the metadata of the token and credential files and the token folder's state, never their contents |
| Mark each credential exchange | Auth: each session refresh and the save that follows |
| Keep every exchange within 40 seconds | Auth: the library's 30-second request timeout plus a local save |
| Capture a `partitioned` unit with its day as `dt` | Capture: every capture of a `daily` unit, fan-out included |

## Obligations on delivery

The image must install `garminconnect` with its `curl_cffi` transport, whose request timeout is a
total limit; the credential-exchange budget in the auth design depends on it.

## References

- `docs/high-level-design.md`: "Garmin goes through `garminconnect` and the consumer sign-in"
- `docs/intent/supervisor/supervisor-design.md`: jobs, exit codes, "Obligations on other
  components"
- grecohome-dagster `docs/intent/garmin/`: the capture and authentication designs this source
  continues, and the Personal API decision
