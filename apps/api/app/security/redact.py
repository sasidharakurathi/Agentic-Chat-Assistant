"""Recognising credentials in free text, for tool results and log lines.

Used by the post-tool step (`app.agent.post_tool`), before a result reaches
the model, and by the structlog processor below, before a log line reaches
stdout (and whatever ships stdout somewhere).

Two ways a credential is recognised (task 6.3 widened both):

- **by its shape**: a vendor's key format, a JWT, a private key block;
- **by where it sits**: after `password=` in a query string or a DSN, as
  the value of a `"token"` key in JSON, on an `Authorization:` line, or
  between `:` and `@` in a connection URI.

The second kind is deliberately tied to tight syntax (`key=value`,
`"key": "value"`, a header line), not to the word appearing in prose: a
tool result that says "reset your password: see the help page" must reach
the model intact.
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
    re.compile(r"\bpa-[A-Za-z0-9_\-]{24,}"),  # Voyage
    re.compile(r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{16,}"),  # Stripe
    re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"),  # Google API key
    re.compile(r"\bglpat-[A-Za-z0-9_\-]{20,}"),  # GitLab
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),  # AWS access key id
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b"),  # GitHub tokens
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"),  # Slack
    re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9/_\-]+"),  # Slack webhook
    re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),  # JWT
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
)
#: `Bearer <token>` in a header-like string.
_BEARER = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/\-]{8,}=*")
#: The password in `scheme://user:password@host`. The user may be empty
#: (`redis://:password@host`).
_URI_PASSWORD = re.compile(r"([a-z][a-z0-9+.\-]*://[^\s:/@]*:)([^\s@/]+)(@)", re.IGNORECASE)
#: The same for database URIs, where a password may contain `/`. Not applied
#: to web addresses: `https://host:8443/users/@me` is a port and a path.
_DB_URI_PASSWORD = re.compile(
    r"((?:postgres(?:ql)?|mysql|mariadb|mongodb|redis|rediss|amqps?|mssql|sqlserver)"
    r"(?:\+[a-z0-9]+)?://[^\s:/@]*:)([^\s@]+)(@)",
    re.IGNORECASE,
)

#: A name that says its value is a credential: `password`, `db_password`,
#: `client_secret`, `aws_secret_access_key`, `x-api-key`, `app_kek`, and
#: `token` alone or as somebody's token. Not `next_page_token` or
#: `tokens_in`: a pagination cursor and a count have to survive.
_NAME = (
    r"(?:[a-z0-9]+[_\-])*(?:password|passwd|pwd|secret|api[_\-]?key|apikey|access[_\-]?key"
    r"|private[_\-]?key|kek)"
    r"|(?:[a-z0-9]+[_\-])*(?:access|refresh|id|auth|bearer|api|session|secret|client|runner)"
    r"[_\-]?token"
    r"|token"
)
#: `password=value` in a query string, a form body or a key/value DSN.
_KEY_EQUALS = re.compile(rf"(?i)(?<![a-z0-9_\-])({_NAME})(=)([^\s&;,\"'<>]+)")
#: `"password": "value"` in JSON (or a Python repr) inside a string.
_KEY_QUOTED = re.compile(rf"""(?i)(["'])({_NAME})\1(\s*:\s*)(["'])(.*?)\4""")
#: A header line that carries a credential. After a scheme that takes one
#: opaque token (`Bearer`, `Basic`, `Token`), only that token goes, and what
#: follows on the line is left to the other rules: a log line can go on to
#: say something else. After `Digest` or `Negotiate`, whose credentials are
#: quoted parameters, everything to the end of the line goes. With no scheme,
#: everything up to a quote (the end of the value inside a JSON string).
_HEADER_LINE = re.compile(
    r"(?im)\b((?:proxy-)?authorization|x-api-key|api-key|x-auth-token)(\s*:\s*)"
    r"((?:bearer|basic|token|digest|negotiate)\s+)?([^\r\n]+)"
)
_ONE_TOKEN = ("bearer", "basic", "token")


def _header(m: re.Match[str]) -> str:
    name, sep, scheme, value = m.group(1), m.group(2), m.group(3), m.group(4)
    if scheme and scheme.strip().lower() in _ONE_TOKEN:
        # A quoted token goes with its quotes.
        token = re.match(r"[\"']?[^\s\"']+[\"']?", value)
        return name + sep + scheme + REDACTED + (value[token.end() :] if token else value)
    if scheme:
        return name + sep + scheme + REDACTED
    quote = re.search(r"[\"']", value)
    return name + sep + REDACTED + (value[quote.start() :] if quote else "")


#: A cookie header, recognised by its `name=value`: "Cookie: 200g flour" in
#: a recipe is not one.
_COOKIE_LINE = re.compile(r"(?im)\b((?:set-)?cookie)(\s*:\s*)([^\r\n\"'=]+=[^\r\n\"']*)")


def strip_secrets(text: str) -> str:
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(REDACTED, text)
    text = _HEADER_LINE.sub(_header, text)
    text = _COOKIE_LINE.sub(lambda m: m.group(1) + m.group(2) + REDACTED, text)
    text = _BEARER.sub(lambda m: m.group(1) + REDACTED, text)
    text = _KEY_QUOTED.sub(
        lambda m: (
            f"{m.group(1)}{m.group(2)}{m.group(1)}{m.group(3)}{m.group(4)}{REDACTED}{m.group(4)}"
        ),
        text,
    )
    text = _KEY_EQUALS.sub(lambda m: m.group(1) + m.group(2) + REDACTED, text)
    text = _DB_URI_PASSWORD.sub(lambda m: m.group(1) + REDACTED + m.group(3), text)
    return _URI_PASSWORD.sub(lambda m: m.group(1) + REDACTED + m.group(3), text)


#: A field whose value is a credential by its name, whatever it looks like:
#: in a log event, a tool's input, a headers dict. Looser than `_NAME`
#: (any token, anything with "secret" or "password" in it), because a key
#: is a name somebody chose, not running text. `tokens_in` is a count and
#: must survive.
_SECRET_KEY = re.compile(
    r"password|passwd|secret|api[_\-]?key|apikey|authorization|cookie|dsn"
    r"|private[_\-]?key|access[_\-]?key|credential|(?:^|[_\-])(?:kek|pwd|token)$"
)
_MAX_DEPTH = 4


_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def is_secret_key(name: object) -> bool:
    """`authToken` and `auth_token` are the same name."""
    if not isinstance(name, str):
        return False
    return _SECRET_KEY.search(_CAMEL.sub("_", name).lower()) is not None


def _scrub(value: Any, depth: int = 0) -> Any:
    if isinstance(value, str):
        return strip_secrets(value)
    if isinstance(value, bytes | BaseException):
        # An exception's text or a raw body can hold a DSN as easily as a
        # string can. Logged as text either way.
        text = value.decode(errors="replace") if isinstance(value, bytes) else repr(value)
        return strip_secrets(text)
    container = isinstance(value, dict) or type(value) in (list, tuple)
    if container and depth >= _MAX_DEPTH:
        return REDACTED  # not looked into, so not let through
    if isinstance(value, dict):
        return {
            k: REDACTED if is_secret_key(k) and v is not None else _scrub(v, depth + 1)
            for k, v in value.items()
        }
    if container:  # a plain list or tuple; namedtuples cannot be rebuilt this way
        return type(value)(_scrub(v, depth + 1) for v in value)
    return value


def redact_event(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """structlog processor: credential-named fields are replaced outright and
    credential-shaped substrings in every other string are masked. Runs last,
    after contextvars and stdlib records have been merged in and after a
    traceback has been rendered to text, so nothing is added after it."""
    scrubbed: dict[str, Any] = _scrub(dict(event_dict))
    return scrubbed


__all__ = ["REDACTED", "is_secret_key", "redact_event", "strip_secrets"]
