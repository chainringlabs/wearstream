"""Requests to Whoop's token endpoint: a fresh client each time and a hard wall-clock deadline
(Whoop auth design, *The refresh request*)."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from wearstream.whoop import http

ATTEMPT_DEADLINE_SECONDS = 30.0  # connect, send and receive together
CONNECT_SECONDS = 5.0
CONNECT_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)


# @spec WHOOP-AUTH-REFRESH-004
def token_client() -> httpx.AsyncClient:
    """A new client (so a new connection) for each token request, never the data client."""
    return httpx.AsyncClient(
        timeout=httpx.Timeout(ATTEMPT_DEADLINE_SECONDS, connect=CONNECT_SECONDS),
        transport=http.TRANSPORT,  # type: ignore[arg-type]
    )


async def _post(form: dict[str, str]) -> httpx.Response:
    async with token_client() as client:
        return await asyncio.wait_for(
            client.post(http.TOKEN_URL, data=form, headers={"Accept": "application/json"}),
            ATTEMPT_DEADLINE_SECONDS,
        )


def post_token(form: dict[str, str]) -> httpx.Response:
    """One token-endpoint request; a deadline overrun raises `TimeoutError`."""
    return asyncio.run(_post(form))


def oauth_error(response: httpx.Response) -> str | None:
    """Only the OAuth `error` code of an error response; nothing else from the body is read."""
    try:
        body: Any = response.json()
    except ValueError:
        return None
    code = body.get("error") if isinstance(body, dict) else None
    return code if isinstance(code, str) else None


def json_object(response: httpx.Response) -> dict[str, Any] | None:
    try:
        body = response.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None
