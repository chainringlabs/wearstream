# Whoop token lifecycle — EARS specs

Design: [auth-design.md](auth-design.md). Prefix `WHOOP-AUTH`. Facets: `STORE`, `BOOT`,
`REFRESH`, `EXCH`, `NEED`, `FP`, `HEALTH`, `LOG`.
"The token file" is `/secrets/whoop/token.json`; "the token lock" is an exclusive `flock` on
`/secrets/whoop/token.json.lock`; "the source lock" is the supervisor's
`/state/locks/whoop.lock`; `R` is the stored refresh token.

## Storage

- [ ] **WHOOP-AUTH-STORE-001**: The Whoop source shall read the client id and client secret from `/secrets/whoop/client_id` and `/secrets/whoop/client_secret`, ignoring surrounding whitespace.
- [ ] **WHOOP-AUTH-STORE-002**: When the Whoop source writes the token file, it shall write the whole file to a temporary file in the same folder created with mode `0600`, fsync it, rename it into place and fsync the folder, keeping every key of the previous file that it does not set.
- [ ] **WHOOP-AUTH-STORE-003**: The Whoop source shall treat a token file as unreadable when it cannot be read or parsed as a JSON object, or lacks `access_token`, `refresh_token` or `expires_at`.

## Bootstrap (`wearstream auth whoop`)

- [ ] **WHOOP-AUTH-BOOT-001**: If `/state/locks` does not exist when `wearstream auth whoop` starts, then the command shall exit with code 2 and a message saying `/state` must be mounted, without contacting Whoop.
- [ ] **WHOOP-AUTH-BOOT-002**: When `wearstream auth whoop` starts, it shall print an authorization URL carrying the client id, the configured redirect URI, the scopes `read:sleep read:workout read:recovery read:cycles read:profile read:body_measurement offline`, a random `state` and an S256 PKCE challenge, and prompt the operator to paste the address the browser was redirected to.
- [ ] **WHOOP-AUTH-BOOT-003**: When the operator pastes text into `wearstream auth whoop`, the command shall strip surrounding whitespace and quotes, ignore any fragment, URL-decode the query values, and accept the text only if it is a URL whose query carries a `code` and a `state` equal to the one it generated.
- [ ] **WHOOP-AUTH-BOOT-004**: If the pasted text carries an `error` parameter, lacks `code` or `state`, has a different `state`, or is not a URL, then `wearstream auth whoop` shall exit with code 2 and a message naming the problem, without exchanging any code.
- [ ] **WHOOP-AUTH-BOOT-005**: After accepting the pasted text and before exchanging the code, `wearstream auth whoop` shall take the source lock with a blocking `flock` that gives up after 10 minutes, printing that it is waiting for a running Whoop unit, and shall exit with code 5 without exchanging the code if it gives up.
- [ ] **WHOOP-AUTH-BOOT-006**: While `wearstream auth whoop` waits for the operator to paste, it shall not hold the source lock or the token lock.
- [ ] **WHOOP-AUTH-BOOT-007**: When `wearstream auth whoop` holds the source lock, it shall exchange the code for tokens and write the result to `/secrets/whoop/token.json.new` as one credential exchange under the token lock, leaving the token file untouched.
- [ ] **WHOOP-AUTH-BOOT-008**: If the code exchange returns no `refresh_token`, or a granted scope list missing any required scope, then `wearstream auth whoop` shall delete the staging file, leave the token file untouched and exit with code 1, naming what is missing.
- [ ] **WHOOP-AUTH-BOOT-009**: After a successful code exchange, `wearstream auth whoop` shall fetch the Whoop profile with the new access token and, if that request fails, delete the staging file, leave the token file untouched and exit with code 1.
- [ ] **WHOOP-AUTH-BOOT-010**: If the profile's user id differs from the `user_id` in an existing token file and the operator did not pass `--new-account`, then `wearstream auth whoop` shall delete the staging file, leave the token file untouched and exit with code 2, naming both user ids.
- [ ] **WHOOP-AUTH-BOOT-011**: When the code exchange, scope check and profile check succeed, `wearstream auth whoop` shall add the profile's `user_id` and `refreshed_at` to the staging file and rename it over the token file atomically under the token lock, release its locks, print the user id and granted scopes, and exit with code 0.
- [ ] **WHOOP-AUTH-BOOT-012**: `wearstream auth whoop` shall never open a listening socket.

## Refresh

- [ ] **WHOOP-AUTH-REFRESH-001**: When a Whoop unit with trigger `schedule` needs a token, it shall refresh only if the stored token's `refreshed_at` is more than 30 minutes ago or absent, or the token is within 5 minutes of its `expires_at`; otherwise it shall use the stored token.
- [ ] **WHOOP-AUTH-REFRESH-002**: When the Whoop source decides to refresh (WHOOP-AUTH-REFRESH-001 or REFRESH-014), it shall acquire its in-process lock and then the token lock, re-read the token file, re-apply the same rule to the re-read token, and refresh only if the rule still calls for it, using the refresh token from that re-read.
- [ ] **WHOOP-AUTH-REFRESH-014**: When a Whoop unit with trigger `catchup`, `retry`, `backfill` or `manual` needs a token, it shall use the stored token while it has not expired, and refresh only once it has expired, `refresh_failed_at` is absent, and the current UTC minute is between 6 and 54 inclusive.
- [ ] **WHOOP-AUTH-REFRESH-015**: If a Whoop unit with trigger `catchup`, `retry`, `backfill` or `manual` holds an expired token that WHOOP-AUTH-REFRESH-014 does not let it refresh, then it shall log `whoop_refresh_deferred` at WARNING and report a failed request.
- [ ] **WHOOP-AUTH-REFRESH-016**: If a Whoop refresh fails with an HTTP error that is not a needed credential, a read or write timeout, the attempt deadline, or any other error after the request may have reached Whoop, then the Whoop source shall record `refresh_failed_at` in the token file; when a refresh succeeds, it shall set `refreshed_at` to now and remove `refresh_failed_at`.
- [ ] **WHOOP-AUTH-REFRESH-003**: When the Whoop source refreshes, it shall `POST` to the token URL with `grant_type=refresh_token`, the client id, the client secret, the refresh token and `scope=offline`.
- [ ] **WHOOP-AUTH-REFRESH-004**: The Whoop source shall send each token-endpoint request (refresh attempt or code exchange) on a new connection not shared with data requests, with a 30-second wall-clock deadline covering connect, send and receive and a 5-second connect limit.
- [ ] **WHOOP-AUTH-REFRESH-005**: Before each refresh attempt, the Whoop source shall create, fsync and remove a temporary file in `/secrets/whoop/`; if that fails, it shall not send the attempt.
- [ ] **WHOOP-AUTH-REFRESH-006**: If a Whoop refresh attempt fails with `ConnectError`, `ConnectTimeout` or `PoolTimeout`, then the Whoop source shall retry the refresh with exponential backoff from 1 to 8 seconds, up to 3 attempts in total.
- [ ] **WHOOP-AUTH-REFRESH-007**: If a Whoop refresh attempt receives any HTTP response other than 200, or fails with any error other than `ConnectError`, `ConnectTimeout` or `PoolTimeout` (including a read or write timeout and the attempt deadline), then the Whoop source shall raise without retrying the refresh.
- [ ] **WHOOP-AUTH-REFRESH-008**: If obtaining a Whoop access token fails for any reason while the Whoop API client prepares a data request, then the client shall raise a `WhoopAuthError` that the data-request retry never retries.
- [ ] **WHOOP-AUTH-REFRESH-009**: When a Whoop refresh returns 200 with `access_token`, a positive integer `expires_in` and a rotated `refresh_token`, the Whoop source shall write the token file with the new access token, the rotated refresh token and an `expires_at` of now plus `expires_in`.
- [ ] **WHOOP-AUTH-REFRESH-010**: If a Whoop refresh returns 200 with `access_token` and a positive integer `expires_in` but no `refresh_token`, then the Whoop source shall keep the stored refresh token, write the new access token and `expires_at`, and log `whoop_token_no_rotation` at WARNING.
- [ ] **WHOOP-AUTH-REFRESH-011**: If a Whoop refresh returns 200 without `access_token` or without a positive integer `expires_in` but with a `refresh_token`, then the Whoop source shall write that refresh token with `expires_at` set to now, and then raise.
- [ ] **WHOOP-AUTH-REFRESH-012**: If a Whoop refresh returns 200 without `access_token` or without a positive integer `expires_in` and without a `refresh_token`, then the Whoop source shall raise without modifying the token file.
- [ ] **WHOOP-AUTH-REFRESH-013**: If a Whoop data request is answered with HTTP 401, then the Whoop source shall set the stored `expires_at` to now and fail that request without retrying it.

## Credential exchanges

- [ ] **WHOOP-AUTH-EXCH-001**: The Whoop source shall treat each token-endpoint request together with the write of its result as a credential exchange, during which a received `SIGTERM` or `SIGINT` is held until the exchange ends.
- [ ] **WHOOP-AUTH-EXCH-002**: If a stop signal is pending, then the Whoop source shall not start a credential exchange.
- [ ] **WHOOP-AUTH-EXCH-004**: The Whoop source shall never wrap a stop signal as a `WhoopAuthError`, retry because of one, or report one as a failed request or a needed credential.
- [ ] **WHOOP-AUTH-EXCH-003**: The Whoop source shall limit the token-file write of a credential exchange to 5 seconds.

## When a person is needed

- [ ] **WHOOP-AUTH-NEED-001**: If the token file does not exist when the Whoop source needs a token, then it shall log `whoop_credential_missing` at ERROR with the action to run `wearstream auth whoop`, and report a needed credential.
- [ ] **WHOOP-AUTH-NEED-002**: If the client id or client secret file is missing or empty when the Whoop source needs a token, then it shall log `whoop_client_credentials_missing` at ERROR with the action to supply both files, and report a needed credential.
- [ ] **WHOOP-AUTH-NEED-003**: If the token file is unreadable (WHOOP-AUTH-STORE-003) when the Whoop source needs a token, then it shall log `whoop_token_unreadable` at ERROR with the action to fix its permissions or run `wearstream auth whoop`, and report a needed credential.
- [ ] **WHOOP-AUTH-NEED-004**: If the refresh pre-check (WHOOP-AUTH-REFRESH-005) fails, or writing a refreshed token fails, then the Whoop source shall log `whoop_token_folder_unwritable` at ERROR with the errno and the action to make `/secrets/whoop` writable by the container user or free space (adding, when a rotated token's write failed, that the grant is lost and `wearstream auth whoop` is needed), and report a needed credential.
- [ ] **WHOOP-AUTH-NEED-005**: If a Whoop refresh is refused with HTTP 400 or 401, then the Whoop source shall read only the OAuth `error` code from the response and report a needed credential, logging at ERROR `whoop_token_invalid_grant` for `invalid_grant`, `whoop_client_rejected` (action: check the client files) for `invalid_client` or `unauthorized_client`, and otherwise `whoop_token_refused` with the status and error code.
- [ ] **WHOOP-AUTH-NEED-006**: If a Whoop data request is answered with HTTP 403, then the Whoop source shall log `whoop_scope_missing` at ERROR with the action to run `wearstream auth whoop` and grant every `read:` scope, and report a needed credential.
- [ ] **WHOOP-AUTH-NEED-007**: If a Whoop refresh fails in any way not covered by WHOOP-AUTH-NEED-001 to NEED-006, then the Whoop source shall log `whoop_token_refresh_failed` at WARNING and report a failed request, not a needed credential.

## Credential fingerprint

- [ ] **WHOOP-AUTH-FP-001**: The Whoop source's credential fingerprint shall be the sha256 of, for the token file and the client id and client secret files, whether each exists and its size, modification time in nanoseconds, inode, mode and owner, followed by the mode and owner of `/secrets/whoop` and whether a temporary file can be created there at that moment, computed without a network call and without reading any file's contents.
- [ ] **WHOOP-AUTH-FP-002**: When the Whoop source reports a needed credential, it shall take the credential fingerprint at that moment and include it in the unit's result.

## Token health check

- [ ] **WHOOP-AUTH-HEALTH-001**: When `whoop_token_health` runs outside the checks component's suppression rule, it shall fail at ERROR with the action to run `wearstream auth whoop` if the token file is missing or unreadable or its `expires_at` is more than 90 minutes in the past, and pass otherwise, without contacting Whoop.
- [ ] **WHOOP-AUTH-HEALTH-003**: `whoop_token_health` shall return its result as a source's own check (CHECKS-RUN-007).
- [ ] **WHOOP-AUTH-HEALTH-002**: While the checks component's suppression rule applies to the Whoop source (waiting for a credential, in a rate-limit cooldown, or capture paused, until the first successful source unit after resuming), `whoop_token_health` shall report `suppressed` with the reason instead of failing.

## Credential-safe logging

- [ ] **WHOOP-AUTH-LOG-001**: If an unexpected failure (anything other than an HTTP error status) occurs on the Whoop authorization-code exchange, refresh or data-request path, then the Whoop source shall log only the event and the exception's type name, with no traceback and no exception text.
- [ ] **WHOOP-AUTH-LOG-002**: When the Whoop source re-raises a failure from the authorization-code exchange, refresh or data-request path, it shall put only the original exception's type name in the new exception's message and suppress the original from the cause chain.
- [ ] **WHOOP-AUTH-LOG-003**: The Whoop source shall never log or print any part, including a prefix, of an access token, refresh token, client secret, authorization code or PKCE verifier.
- [ ] **WHOOP-AUTH-LOG-004**: The Whoop source shall never log the body of a token-endpoint response or of an API error response, reading from a token-endpoint error response only its OAuth `error` code.
