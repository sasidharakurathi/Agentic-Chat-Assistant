"""Recognising credentials in free text, for tool results and log lines.

Used by the post-tool step (`app.agent.post_tool`), before a result reaches
the model, and by the structlog processor below, before a log line reaches
stdout (and whatever ships stdout somewhere).
"""

from __future__ import annotations

import re
from collections.abc import MutableMapping
from typing import Any

REDACTED = "<redacted>"

#: Shapes of credentials worth recognising. Deliberately specific: a pattern
#: that matched any long random string would mangle ids, hashes and data.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),  # Anthropic
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{32,}"),  # OpenAI-style
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),  # AWS access key id
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b"),  # GitHub tokens
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"),  # Slack
    re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),  # JWT
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
)
#: `Bearer <token>` in a header-like string.
_BEARER = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/\-]{16,}=*")
#: The password in `scheme://user:password@host`.
_URI_PASSWORD = re.compile(r"([a-z][a-z0-9+.\-]*://[^\s:/@]+:)([^\s@/]+)(@)", re.IGNORECASE)


def strip_secrets(text: str) -> str:
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(REDACTED, text)
    text = _BEARER.sub(lambda m: m.group(1) + REDACTED, text)
    return _URI_PASSWORD.sub(lambda m: m.group(1) + REDACTED + m.group(3), text)


#: Log fields whose value is a credential by name, whatever it looks like.
#: Matched against the whole key, lowercased: "tokens_in" is a count, not a
#: token, and must survive.
_SECRET_KEYS = re.compile(
    r"(?:password|passwd|secret|api_key|apikey|authorization|cookie|dsn"
    r"|(?:access|refresh|id|auth|bearer)_token|token|private_key|kek)",
)
_MAX_DEPTH = 4


def _scrub(value: Any, depth: int = 0) -> Any:
    if isinstance(value, str):
        return strip_secrets(value)
    if depth >= _MAX_DEPTH:
        return value
    if isinstance(value, dict):
        return {
            k: REDACTED
            if isinstance(k, str) and _SECRET_KEYS.fullmatch(k.lower()) and v is not None
            else _scrub(v, depth + 1)
            for k, v in value.items()
        }
    if type(value) in (list, tuple):  # not namedtuples: they cannot be rebuilt this way
        return type(value)(_scrub(v, depth + 1) for v in value)
    return value


def redact_event(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """structlog processor: credential-named fields are replaced outright and
    credential-shaped substrings in every other string are masked. Runs last,
    after contextvars and stdlib records have been merged in, so nothing is
    added after it."""
    scrubbed: dict[str, Any] = _scrub(dict(event_dict))
    return scrubbed


__all__ = ["REDACTED", "redact_event", "strip_secrets"]
