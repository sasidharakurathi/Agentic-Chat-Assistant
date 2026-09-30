"""Personal data: find it and redact it (task 5.3).

With `guardrails.pii_redaction` on (the default), personal data is replaced
with a placeholder where it would leave for somewhere the builder did not
choose:

- **web search queries**, which go to a public search provider;
- **traces**, the prompt, answer and tool payloads copied to the tracing
  backend.

It is deliberately not applied to the assistant's own databases, the
knowledge base, or HTTP and MCP calls to systems the builder allowlisted or
registered: looking a customer up by email is the job there, and redacting
it would just break the tool.

Pattern-based, so it finds well-formed values (an email, a card number that
passes the Luhn check, a phone number with enough digits), not "my
neighbour's name". Each kind keeps a stable placeholder, like `[email]`, so
the redacted text still reads.
"""

from __future__ import annotations

import re
from collections.abc import Callable

_EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
# Card numbers: 13-19 digits, optionally grouped; confirmed by Luhn below.
_CARD = re.compile(r"\b(?:\d[ \-]?){12,18}\d\b")
_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
# India: Aadhaar (12 digits, never starting 0 or 1, often grouped 4-4-4) and PAN.
# Not part of a longer run of digit groups: the first 12 digits of a
# 16-digit card-shaped number are not an Aadhaar number.
_AADHAAR = re.compile(r"(?<![\d][ \-])(?<!\d)[2-9]\d{3}[ \-]?\d{4}[ \-]?\d{4}(?![ \-]?\d)")
_PAN = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
# Phone numbers, by shape rather than length (a 13-digit order number is
# not one): international with +, a grouped (555) 123-4567 style, or a
# 10-digit Indian mobile number.
_PHONE = re.compile(
    r"(?<![\w+])\+\d{1,3}[\s\-]?\(?\d{1,4}\)?(?:[\s\-.]?\d{2,5}){2,4}\b"
    r"|\(?\b\d{3}\)?[\s\-.]\d{3}[\s\-.]\d{4}\b"
    r"|\b[6-9]\d{9}\b"
)


def _luhn(number: str) -> bool:
    digits = [int(c) for c in number if c.isdigit()]
    if not 13 <= len(digits) <= 19:  # noqa: PLR2004 - card number lengths
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        doubled = d * 2
        total += (doubled - 9 if doubled > 9 else doubled) if i % 2 == 1 else d  # noqa: PLR2004
    return total % 10 == 0


def _enough_digits(value: str) -> bool:
    return sum(c.isdigit() for c in value) >= 10  # noqa: PLR2004 - a real phone number


#: kind -> (pattern, extra check). Order matters: the most specific first, so
#: a card number is redacted as a card before the phone pattern sees it.
_KINDS: list[tuple[str, re.Pattern[str], Callable[[str], bool] | None]] = [
    ("email", _EMAIL, None),
    ("card", _CARD, _luhn),
    ("ssn", _SSN, None),
    ("aadhaar", _AADHAAR, None),
    ("pan", _PAN, None),
    ("phone", _PHONE, _enough_digits),
]


def redact(text: str) -> tuple[str, list[str]]:
    """`text` with personal data replaced by `[kind]`, and the kinds found."""
    found: list[str] = []
    for kind, pattern, check in _KINDS:

        def sub(
            m: re.Match[str], kind: str = kind, check: Callable[[str], bool] | None = check
        ) -> str:
            if check is not None and not check(m.group(0)):
                return m.group(0)
            if kind not in found:
                found.append(kind)
            return f"[{kind}]"

        text = pattern.sub(sub, text)
    return text, found


__all__ = ["redact"]
