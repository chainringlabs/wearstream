"""No event WearStream defines has a field the redaction filter would hide (OPERATOR-REDACT-002).

The redaction filter (OPERATOR-REDACT-001) is the backstop; this keeps it from ever being the
reason an event loses information, by checking every logging call and `bind` in the source."""

from __future__ import annotations

import ast
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[2] / "src" / "wearstream"
LOGGING = {"debug", "info", "warning", "error", "exception", "critical", "bind"}
WORDS = ("password", "passwd", "secret", "token", "apikey", "api_key", "authorization",
         "credential", "private_key", "privatekey", "signature", "sessionid", "session_id",
         "cookie")  # fmt: skip


def _event_fields() -> list[tuple[str, int, str]]:
    found = []
    for path in sorted(SOURCE.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in LOGGING:
                    found += [(str(path), node.lineno, k.arg) for k in node.keywords if k.arg]
    return found


# @spec OPERATOR-REDACT-002
def test_no_event_field_is_named_like_a_credential() -> None:
    fields = _event_fields()
    assert len(fields) > 100  # the scan sees the logging calls
    offending = [f for f in fields if any(word in f[2].lower() for word in WORDS)]
    assert offending == []
