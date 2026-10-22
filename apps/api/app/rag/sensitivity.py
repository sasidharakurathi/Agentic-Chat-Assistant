"""What a document holds that shouldn't reach everyone (Phase 9.4).

Before a source open to everyone is indexed, its text is scanned for
credentials and for Indian identity numbers (Aadhaar, PAN) and card numbers,
with the detectors the chat guardrails already use. One credential, or a few
identity or card numbers, hold the source back: its builder either gives it
to groups or confirms that everyone may read it, and the confirmation is in
the audit log. Phone numbers and emails are counted but never hold anything
back: a handbook is full of the IT desk's number.

Pattern-based, so it misses what has no shape and flags PAN-like product
codes: the matches are shown (masked) so the builder can judge.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any

from app.guardrails.pii import find
from app.security.redact import find_secrets

#: Kinds that hold a source back once there are this many.
ID_KINDS = ("aadhaar", "pan", "card")
IDS_TO_HOLD = 3
#: How many masked examples are kept for the builder.
SAMPLES = 8


@dataclass
class SensitivityReport:
    #: kind -> how many (secret, aadhaar, pan, card, phone, email, ssn).
    counts: dict[str, int] = field(default_factory=dict)
    #: A few masked examples: {"kind", "value"}.
    samples: list[dict[str, str]] = field(default_factory=list)
    #: Why it is held back, in words; empty when it isn't.
    reason: str = ""
    #: The content these were found in, so a confirmation is for this text.
    checksum: str = ""

    @property
    def flagged(self) -> bool:
        return bool(self.reason)

    def as_json(self) -> dict[str, Any]:
        return asdict(self)


def mask(kind: str, value: str) -> str:
    """Enough to recognise, not enough to use."""
    if kind == "secret":
        return value[:4] + "…" if len(value) > 4 else "…"  # noqa: PLR2004
    chars = [c for c in value if c.isalnum()]
    keep = 4 if len(chars) > 6 else 1  # noqa: PLR2004
    return "•" * (len(chars) - keep) + "".join(chars[-keep:])


def scan(text: str, checksum: str = "") -> SensitivityReport:
    found = [("secret", s) for s in find_secrets(text)] + find(text)
    counts = Counter(kind for kind, _ in found)
    report = SensitivityReport(counts=dict(sorted(counts.items())), checksum=checksum)
    shown = [f for f in found if f[0] in ("secret", *ID_KINDS)] or found
    report.samples = [{"kind": k, "value": mask(k, v)} for k, v in shown[:SAMPLES]]
    ids = sum(counts[k] for k in ID_KINDS)
    if counts["secret"]:
        report.reason = (
            f"{counts['secret']} credential(s), such as an API key or password, are in it"
        )
    elif ids >= IDS_TO_HOLD:
        report.reason = f"{ids} Aadhaar, PAN or card numbers are in it"
    return report


__all__ = ["IDS_TO_HOLD", "SensitivityReport", "mask", "scan"]
