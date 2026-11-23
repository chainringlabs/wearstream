# Whoop capture — EARS specs

Design: [capture-design.md](capture-design.md). Prefix `WHOOP-CAPTURE`. Facets: `JOB`,
`RANGE`, `SNAP`, `REQ`, `PAGE`, `TIME`, `STOP`, `REPORT`, `CHECKS`.
"A `range` unit" captures one partition day `D`; "a `snapshots` unit" captures the two
snapshot collections; both are child processes started by the supervisor or by hand.

## Jobs

- [ ] **WHOOP-CAPTURE-JOB-001**: The Whoop source shall declare a `partitioned` job `range` with schedule `17 * * * *`, a window of 8 days including the fire date, collections `sleep`, `recovery`, `workout` and `cycle`, a time limit of 5 minutes and a backfill pause of 60 seconds.
- [ ] **WHOOP-CAPTURE-JOB-002**: The Whoop source shall declare a `snapshot` job `snapshots` with schedule `17 * * * *` and a time limit of 5 minutes.
- [ ] **WHOOP-CAPTURE-JOB-003**: The Whoop source shall declare a `checks` job `checks` with schedule `37 * * * *` and a time limit of 10 minutes.

## The `range` unit

- [ ] **WHOOP-CAPTURE-RANGE-001**: When a `range` unit runs for day `D`, it shall request `sleep` (`/developer/v2/activity/sleep`), `recovery` (`/developer/v2/recovery`), `workout` (`/developer/v2/activity/workout`) and `cycle` (`/developer/v2/cycle`) in that order, each with `start` set to `D` 00:00Z and `end` to the next day's 00:00Z, both formatted `YYYY-MM-DDTHH:MM:SS.mmmZ`.
- [ ] **WHOOP-CAPTURE-RANGE-002**: When a `range` unit receives a page holding at least one record, it shall capture the response bytes to collection `<collection>` with `dt` set to `D`, skip-identical on in folder scope, `request_url` set to the final request URL including its query string, and `request_params` set to that URL's decoded query as an object of string values, keeping the last value of a repeated key.
- [ ] **WHOOP-CAPTURE-RANGE-003**: If a `range` unit receives a page holding zero records, then it shall not capture it.
- [ ] **WHOOP-CAPTURE-RANGE-004**: If a Whoop data request returns an HTTP error status, then the Whoop source shall not capture the response body.
- [ ] **WHOOP-CAPTURE-RANGE-005**: If a Whoop data request returns a 2xx body that is not valid JSON, then the Whoop source shall not capture it and shall fail that request without retrying it.
- [ ] **WHOOP-CAPTURE-RANGE-006**: If one collection's requests fail in a `range` unit, then the unit shall still request the remaining collections, unless the failure is a needed credential.

## The `snapshots` unit

- [ ] **WHOOP-CAPTURE-SNAP-001**: When a `snapshots` unit runs, it shall request `profile` (`/developer/v2/user/profile/basic`) and then `body_measurement` (`/developer/v2/user/measurement/body`), capturing each successful response with no `dt` and skip-identical on in folder scope.
- [ ] **WHOOP-CAPTURE-SNAP-002**: If the `profile` request of a `snapshots` unit fails, then the unit shall not request `body_measurement`.

## Data requests

- [ ] **WHOOP-CAPTURE-REQ-001**: The Whoop source shall send data requests to `https://api.prod.whoop.com` with a 30-second timeout, reusing one HTTP client per unit.
- [ ] **WHOOP-CAPTURE-REQ-002**: The Whoop source shall pass every data-request attempt, retries included, through a sliding-window rate limiter per unit set to 90% of the configured requests per minute (default 60).
- [ ] **WHOOP-CAPTURE-REQ-003**: If a Whoop data request times out, fails with a network error, or returns HTTP 429, 500, 502, 503 or 504, then the Whoop source shall retry it, up to 3 attempts in total, waiting the response's `Retry-After` seconds (capped at 60) when present and 2 seconds otherwise.
- [ ] **WHOOP-CAPTURE-REQ-004**: If a Whoop data request returns an HTTP error status other than 401, 403, 429, 500, 502, 503 or 504, or fails with a protocol error that is not a network error, then the Whoop source shall fail it without retrying.
- [ ] **WHOOP-CAPTURE-REQ-005**: The Whoop data-request retry shall never retry a `WhoopAuthError`.

## Pagination

- [ ] **WHOOP-CAPTURE-PAGE-001**: The Whoop source shall request range-collection pages with `limit` 25 and send each response's `next_token` as the `nextToken` parameter of the next request.
- [ ] **WHOOP-CAPTURE-PAGE-002**: When a range-collection response has no `next_token` or holds zero records, the Whoop source shall end pagination for that collection.
- [ ] **WHOOP-CAPTURE-PAGE-003**: If a range-collection response carries a `next_token` already seen in the same pagination, then the Whoop source shall end pagination with a WARNING, keep the pages already captured, and count the collection as successful.
- [ ] **WHOOP-CAPTURE-PAGE-004**: If a range-collection pagination has fetched 20 pages, then the Whoop source shall end pagination with a WARNING, keep the pages already captured, and count the collection as successful.

## Time limit

- [ ] **WHOOP-CAPTURE-TIME-001**: A Whoop unit shall start no request attempt (data or token) and no wait (a retry pause or a `Retry-After`) that could still be in progress when less than 60 seconds remain before its deadline (its start plus its job's time limit).
- [ ] **WHOOP-CAPTURE-TIME-002**: When a Whoop unit stops starting work because of its deadline (WHOOP-CAPTURE-TIME-001), it shall report the collection in progress and every collection not yet captured as failed requests and end.

## Stop signals

- [ ] **WHOOP-CAPTURE-STOP-001**: While a stop signal is pending, a Whoop unit shall start no request attempt and no wait, shall not move on to another collection, and shall end its job.

## What the unit reports

- [ ] **WHOOP-CAPTURE-REPORT-001**: When a Whoop data request fails after its retries, or fails without retry, the Whoop source shall report a failed request for that collection.
- [ ] **WHOOP-CAPTURE-REPORT-002**: When a Whoop unit receives a needed-credential report from the token lifecycle, it shall make no further requests and report the needed credential.
- [ ] **WHOOP-CAPTURE-REPORT-003**: When a Whoop unit receives a token failure that is not a needed credential, it shall make no further requests and report a failed request for each collection not yet captured.

## Collection checks

- [ ] **WHOOP-CAPTURE-CHECKS-001**: The Whoop source shall configure, for each of `sleep`, `recovery` and `cycle`, a freshness check (newest capture within 26 hours plus 6 hours grace), a completeness check (event-date gap over 2 days; event date `start` for `sleep` and `cycle`, `created_at` for `recovery`), a schema-drift check and a content-health check.
- [ ] **WHOOP-CAPTURE-CHECKS-002**: The Whoop source shall configure, for `workout`, only a completeness check (event-date gap over 14 days, event date `start`, including the gap from the newest workout to now) and a schema-drift check.
- [ ] **WHOOP-CAPTURE-CHECKS-003**: The Whoop source shall configure, for each of `profile` and `body_measurement`, a freshness check (newest capture within 26 hours plus 6 hours grace), a schema-drift check and a content-health check, and no completeness check.
- [ ] **WHOOP-CAPTURE-CHECKS-004**: The Whoop `checks` job shall run the collection checks and `whoop_token_health`.
- [ ] **WHOOP-CAPTURE-CHECKS-005**: The Whoop source shall configure every completeness check at warning severity.
