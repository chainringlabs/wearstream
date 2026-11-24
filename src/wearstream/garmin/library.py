"""The one module that imports `garminconnect`: building clients, the token path, the
environment and logger rules every process applies first (Garmin auth design)."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import Any

from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)
from garminconnect.client.tokens import token_file_path

__all__ = [
    "DOWNLOAD_FORMAT",
    "Garmin",
    "GarminConnectAuthenticationError",
    "GarminConnectConnectionError",
    "GarminConnectTooManyRequestsError",
    "make_client",
    "prepare",
    "token_file_path",
]

DOWNLOAD_FORMAT = Garmin.ActivityDownloadFormat
LIBRARY_LOGGER = "garminconnect"


# @spec GARMIN-AUTH-STORE-002, GARMIN-AUTH-LOG-003
def prepare(values: dict[str, Any], log: Any) -> None:
    """Before using the library: drop `GARMINTOKENS` and silence the library's loggers."""
    if os.environ.pop("GARMINTOKENS", None) is not None:
        log.warning(
            "garmin_tokens_env_ignored",
            reason="GARMINTOKENS was set; WearStream uses only /secrets/garmin",
            action="Remove GARMINTOKENS from the container's environment.",
        )
    library = logging.getLogger(LIBRARY_LOGGER)
    if values.get("WEARSTREAM_GARMIN_LIBRARY_DEBUG"):
        library.setLevel(logging.DEBUG)
    else:
        library.setLevel(logging.CRITICAL + 1)
        library.propagate = False


# @spec GARMIN-AUTH-STORE-004
def make_client(
    values: dict[str, Any],
    email: str | None = None,
    password: str | None = None,
    prompt_mfa: Callable[[], str] | None = None,
) -> Any:
    """A client for Garmin's global or China site; without credentials it can only resume."""
    return Garmin(
        email=email,
        password=password,
        is_cn=bool(values.get("WEARSTREAM_GARMIN_CHINA")),
        prompt_mfa=prompt_mfa,
    )
