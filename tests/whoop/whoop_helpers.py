"""Helpers shared by the Whoop tests: a fake Whoop (token endpoint and API) behind an httpx
MockTransport, a fake clock, token files, and a log collector."""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx

from wearstream.state import iso

NOW = dt.datetime(2026, 11, 24, 10, 17, 5, tzinfo=dt.UTC)  # minute 17, a scheduled tick
SCOPES = (
    "read:sleep read:workout read:recovery read:cycles read:profile read:body_measurement offline"
)
TOKEN_PATH = "/oauth/oauth2/token"
PATHS = {
    "sleep": "/developer/v2/activity/sleep",
    "recovery": "/developer/v2/recovery",
    "workout": "/developer/v2/activity/workout",
    "cycle": "/developer/v2/cycle",
    "profile": "/developer/v2/user/profile/basic",
    "body_measurement": "/developer/v2/user/measurement/body",
}
COLLECTION_OF = {path: name for name, path in PATHS.items()}
Reply = httpx.Response | BaseException | Callable[[httpx.Request], httpx.Response]


class FakeClock:
    """Wall clock and monotonic clock moving together; `sleep` advances both."""

    def __init__(self, start: dt.datetime = NOW) -> None:
        self.start, self.mono = start, 0.0
        self.slept: list[float] = []

    def utc_now(self) -> dt.datetime:
        return self.start + dt.timedelta(seconds=self.mono)

    def monotonic(self) -> float:
        return self.mono

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.mono += seconds

    def advance(self, seconds: float) -> None:
        self.mono += seconds

    def set_wall(self, moment: dt.datetime) -> None:
        self.start = moment - dt.timedelta(seconds=self.mono)


def page(*records: dict[str, Any], next_token: str | None = None) -> dict[str, Any]:
    return {"records": list(records), "next_token": next_token}


def reply(status: int = 200, body: Any = None, **headers: str) -> httpx.Response:
    content = body if isinstance(body, bytes) else json.dumps(body if body is not None else {})
    return httpx.Response(
        status, content=content, headers={k.replace("_", "-"): v for k, v in headers.items()}
    )


class FakeWhoop:
    """Answers each path from its script (in order; the last entry repeats), or a default:
    the token endpoint rotates the refresh token, a range endpoint returns one record, a
    snapshot endpoint returns an object."""

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.requests: list[httpx.Request] = []
        self.scripts: dict[str, list[Reply]] = {}
        self.issued = 0
        self.per_request_seconds = 0.0

    def script(self, path: str, *replies: Reply) -> None:
        self.scripts[PATHS.get(path, path)] = list(replies)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self.clock.advance(self.per_request_seconds)
        script = self.scripts.get(request.url.path)
        if script:
            answer = script.pop(0) if len(script) > 1 else script[0]
            if isinstance(answer, BaseException):
                raise answer
            return answer(request) if callable(answer) else answer
        return self.default(request)

    def default(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == TOKEN_PATH:
            self.issued += 1
            body = {
                "access_token": f"AT-{self.issued}",
                "refresh_token": f"RT-{self.issued}",
                "expires_in": 3600,
                "scope": SCOPES,
                "token_type": "bearer",
            }
            return reply(200, body)
        name = COLLECTION_OF.get(request.url.path)
        if name in ("profile", "body_measurement"):
            return reply(
                200,
                {"user_id": 101, "first_name": "A"} if name == "profile" else {"height_meter": 1.8},
            )
        if name:
            return reply(200, page({"id": f"{name}-1", "start": request.url.params.get("start")}))
        return reply(404, {"error": "no such path"})

    # What was asked
    def token_forms(self) -> list[dict[str, str]]:
        return [
            {k: v[-1] for k, v in parse_qs(r.content.decode()).items()}
            for r in self.requests
            if r.url.path == TOKEN_PATH
        ]

    def data_requests(self, collection: str | None = None) -> list[httpx.Request]:
        return [
            r
            for r in self.requests
            if r.url.path != TOKEN_PATH
            and (collection is None or COLLECTION_OF.get(r.url.path) == collection)
        ]

    def collections_requested(self) -> list[str]:
        order: list[str] = []
        for r in self.data_requests():
            name = COLLECTION_OF[r.url.path]
            if not order or order[-1] != name:
                order.append(name)
        return order


def write_client(folder: Path, client_id: str = "client-abc", secret: str = "CS-SECRET") -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "client_id").write_text(f"  {client_id}\n")
    (folder / "client_secret").write_text(f"{secret}\n")


def write_token(folder: Path, clock: FakeClock, **fields: Any) -> dict[str, Any]:
    """A valid token, refreshed 10 minutes ago and expiring in 50; `None` removes a field."""
    now = clock.utc_now()
    doc: dict[str, Any] = {
        "access_token": "AT-0",
        "refresh_token": "RT-0",
        "token_type": "bearer",
        "expires_at": iso(now + dt.timedelta(minutes=50)),
        "refreshed_at": iso(now - dt.timedelta(minutes=10)),
        "scopes": SCOPES.split(),
        "user_id": "101",
    }
    doc.update(fields)
    doc = {k: v for k, v in doc.items() if v is not None}
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "token.json").write_text(json.dumps(doc))
    return doc


def read_token(folder: Path) -> dict[str, Any]:
    return json.loads((folder / "token.json").read_text())


def minutes(clock: FakeClock, delta: float) -> str:
    return iso(clock.utc_now() + dt.timedelta(minutes=delta))


class Logs:
    def __init__(self) -> None:
        self.lines: list[bytes] = []

    def events(self, name: str | None = None) -> list[dict[str, Any]]:
        parsed = [json.loads(line) for line in self.lines]
        return [e for e in parsed if name is None or e["event"] == name]

    def names(self) -> list[str]:
        return [e["event"] for e in self.events()]

    def text(self) -> str:
        return b"".join(self.lines).decode()
