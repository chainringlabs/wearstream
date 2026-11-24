"""Removing credential-named keys from a parsed payload before it is captured."""

from __future__ import annotations

from typing import Any

CREDENTIAL_WORDS = (
    "password", "passwd", "secret", "token", "apikey", "api_key", "authorization", "credential",
    "private_key", "privatekey", "signature", "sessionid", "session_id",
)  # fmt: skip


def _is_credential(key: object) -> bool:
    lowered = str(key).lower()
    return any(word in lowered for word in CREDENTIAL_WORDS)


def _screen(value: Any, path: str, removed: list[str]) -> Any:
    if isinstance(value, dict):
        kept = {}
        for key, item in value.items():
            child = f"{path}.{key}" if path else str(key)
            if _is_credential(key):
                removed.append(child)
            else:
                kept[key] = _screen(item, child, removed)
        return kept
    if isinstance(value, list):
        return [_screen(item, f"{path}[{i}]", removed) for i, item in enumerate(value)]
    return value


# @spec CAPTURE-SCREEN-001, CAPTURE-SCREEN-002
def screen_secrets(value: Any) -> tuple[Any, list[str]]:
    """A copy of `value` without credential-named keys, and the paths of the keys removed."""
    removed: list[str] = []
    return _screen(value, "", removed), removed
