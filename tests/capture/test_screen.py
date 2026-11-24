"""Removing credential-named keys from a parsed payload (CAPTURE-SCREEN)."""

from __future__ import annotations

import copy

import pytest

from wearstream.capture import screen_secrets

WORDS = [
    "password", "passwd", "secret", "token", "apikey", "api_key", "authorization", "credential",
    "private_key", "privatekey", "signature", "sessionid", "session_id",
]  # fmt: skip


# @spec CAPTURE-SCREEN-001
def test_credential_keys_are_removed_at_any_depth_with_their_paths() -> None:
    payload = {
        "profile": {"accessToken": "a", "name": "n"},
        "devices": [{"authToken": "t", "id": 1}, {"id": 2}],
        "Password": "p",
        "ok": True,
    }
    clean, removed = screen_secrets(payload)
    assert clean == {"profile": {"name": "n"}, "devices": [{"id": 1}, {"id": 2}], "ok": True}
    assert sorted(removed) == ["Password", "devices[0].authToken", "profile.accessToken"]


# @spec CAPTURE-SCREEN-001
@pytest.mark.parametrize("word", WORDS)
def test_every_listed_word_matches_in_any_case(word: str) -> None:
    key = f"my{word.upper()}Field"
    clean, removed = screen_secrets([{key: 1, "keep": 2}])
    assert clean == [{"keep": 2}]
    assert removed == [f"[0].{key}"]


# @spec CAPTURE-SCREEN-001
def test_a_value_without_credentials_comes_back_equal_with_no_paths() -> None:
    payload = {"sleep": [{"score": 80}], "total": 1, "name": None}
    assert screen_secrets(payload) == (payload, [])


# @spec CAPTURE-SCREEN-002
def test_the_input_is_not_modified() -> None:
    payload = {"a": {"token": 1, "b": [{"secret": 2}]}}
    original = copy.deepcopy(payload)
    screen_secrets(payload)
    assert payload == original
