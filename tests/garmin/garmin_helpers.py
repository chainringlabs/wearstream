"""Helpers shared by the Garmin tests: a stand-in for `garminconnect.Garmin` whose sign-in,
session refresh and data calls a test scripts, a fake clock, and a log collector."""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
)

NOW = dt.datetime(2026, 11, 24, 5, 30, 5, tzinfo=dt.UTC)
SESSION = {"di_token": "DI-TOKEN-1", "di_refresh_token": "DI-REFRESH-1", "di_client_id": "cid-1"}
PASSWORD, EMAIL = "PW-SECRET-1", "runner@example.test"


class FakeClock:
    def __init__(self) -> None:
        self.mono = 0.0
        self.slept: list[float] = []

    def utc_now(self) -> dt.datetime:
        return NOW + dt.timedelta(seconds=self.mono)

    def monotonic(self) -> float:
        return self.mono

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.mono += seconds


class FakeInner:
    """`Garmin.client`: the session, `dumps()`, and the library's refresh, which swallows its
    own failures as the real one does."""

    def __init__(self, library: FakeLibrary, session: dict[str, Any] | None) -> None:
        self.library, self.session = library, dict(session or {})
        self._tokenstore_path: str | None = None

    def dumps(self) -> str:
        return json.dumps(self.session)

    def _refresh_di_token(self) -> None:
        status = self.library.refresh_status
        if status != 200:
            raise GarminConnectAuthenticationError(f"DI token refresh failed: {status}")
        self.session = {**self.session, "di_refresh_token": "DI-REFRESH-ROTATED"}

    def _refresh_session(self) -> None:
        self.library.refreshes += 1
        if self.library.during_refresh:
            self.library.during_refresh()
        try:
            self._refresh_di_token()
            if self._tokenstore_path:
                write_session(Path(self._tokenstore_path), self.session)
        except Exception:
            pass


class FakeGarmin:
    def __init__(
        self,
        email: str | None = None,
        password: str | None = None,
        is_cn: bool = False,
        prompt_mfa: Callable[[], str] | None = None,
        **_options: Any,
    ) -> None:
        self.library: FakeLibrary = FakeGarmin.library
        self.email, self.password, self.is_cn, self.prompt_mfa = email, password, is_cn, prompt_mfa
        self.client = FakeInner(self.library, None)
        self.display_name: str | None = None
        self.profile_id: int | None = None
        self.library.clients.append(self)

    library: FakeLibrary

    def login(self, tokenstore: str | None = None) -> tuple[None, None]:
        lib = self.library
        lib.logins.append(("password" if self.password else "resume", tokenstore))
        if self.password:
            self._password_login(tokenstore)
        else:
            self._resume(tokenstore)
        self.display_name, self.profile_id = lib.profile["display_name"], lib.profile["profile_id"]
        return None, None

    def _resume(self, tokenstore: str | None) -> None:
        lib = self.library
        try:
            stored = json.loads(
                (Path(tokenstore or "/nonexistent") / "garmin_tokens.json").read_text()
            )
        except OSError, ValueError:
            raise GarminConnectAuthenticationError("Username and password are required") from None
        self.client.session, self.client._tokenstore_path = stored, tokenstore
        if lib.refresh_on_resume:
            self.client._refresh_session()
        outcome = lib.resume.pop(0) if len(lib.resume) > 1 else lib.resume[0]
        if isinstance(outcome, BaseException):
            raise outcome
        if callable(outcome):
            outcome()

    def _password_login(self, tokenstore: str | None) -> None:
        lib = self.library
        if lib.password_login == "mfa":
            try:
                code = self.prompt_mfa() if self.prompt_mfa else None
            except Exception as exc:
                raise GarminConnectConnectionError("Login failed: re-wrapped") from exc
            if code != lib.mfa_code:
                raise GarminConnectAuthenticationError("MFA verification failed: wrong code")
        elif isinstance(lib.password_login, BaseException):
            raise lib.password_login
        self.client.session = dict(lib.session)
        if tokenstore and lib.store_writable:
            write_session(Path(tokenstore), lib.session)

    def __getattr__(self, name: str) -> Any:
        if not name.startswith(("get_", "download_")):
            raise AttributeError(name)

        def call(*args: Any, **kwargs: Any) -> Any:
            return self.library.answer(name, args, kwargs)

        return call


class FakeLibrary:
    """What the stand-in Garmin does: `resume` (a list of outcomes, the last repeating),
    `password_login` (`"ok"`, `"mfa"` or an exception), and `answers` per method."""

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.clients: list[FakeGarmin] = []
        self.logins: list[tuple[str, str | None]] = []
        self.resume: list[Any] = ["ok"]
        self.password_login: Any = "ok"
        self.mfa_code = "123456"
        self.session = dict(SESSION)
        self.profile = {"display_name": "Runner", "profile_id": 12345}
        self.answers: dict[str, Any] = {}
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any], float]] = []
        self.refresh_on_resume = False
        self.refresh_status = 200
        self.refreshes = 0
        self.during_refresh: Callable[[], None] | None = None
        self.store_writable = True
        self.per_call_seconds = 0.0

    def answer(self, name: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        self.calls.append((name, args, kwargs, self.clock.mono))
        self.clock.mono += self.per_call_seconds
        found = self.answers.get(name, default_answer(name))
        if isinstance(found, BaseException):
            raise found
        return found(*args, **kwargs) if callable(found) else found

    def methods(self) -> list[str]:
        return [name for name, *_rest in self.calls]


def default_answer(name: str) -> Any:
    if name == "get_devices":
        return [{"deviceId": 111}]
    if name == "get_userprofile_settings":
        return {"id": 777, "displayName": "Runner"}
    if name == "get_activities_by_date":
        return []
    if name == "download_activity":
        return b"PK\x03\x04fit-bytes"
    return {"value": name}


def write_session(folder: Path, session: dict[str, Any]) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "garmin_tokens.json"
    path.write_text(json.dumps(session))
    path.chmod(0o600)
    return path


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
