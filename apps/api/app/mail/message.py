"""A message, and the cleaning every field put into one goes through."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import make_msgid
from typing import Any

#: Anything that reads as a web address or an email link. A name containing
#: one would put a clickable link, chosen by whoever named the thing, into
#: a message from the platform.
_LINKISH = re.compile(
    r"(?i)\b(?:https?://|www\.|mailto:)\S*|\S+\.(?:com|net|org|io|in|co|app|dev|xyz)\S*"
)
_SPACE = re.compile(r"\s+")
MAX_FIELD = 120


def clean_field(value: str | None) -> str:
    """A name or label as it may appear in a message: one line (no header
    can be forged), no web addresses, and not too long."""
    text = _SPACE.sub(" ", str(value or "")).strip()
    text = _LINKISH.sub("[link removed]", text)
    return text[:MAX_FIELD].strip()


@dataclass(frozen=True)
class Mail:
    """One message to one person."""

    to: str
    subject: str
    text: str
    #: What it is, for the record and the log: `invite`, `version_submitted`,
    #: `version_reviewed`, `test`.
    kind: str

    def __post_init__(self) -> None:
        # The subject becomes a header: one line, whatever built it.
        if "\n" in self.subject or "\r" in self.subject:
            raise ValueError("a subject is one line")
        if "\n" in self.to or "\r" in self.to:
            raise ValueError("an address is one line")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Mail:
        return cls(**{k: data[k] for k in ("to", "subject", "text", "kind")})

    def to_email(self, *, sender: str, sender_name: str) -> EmailMessage:
        msg = EmailMessage()
        msg["From"] = Address(display_name=clean_field(sender_name), addr_spec=sender)
        msg["To"] = self.to
        msg["Subject"] = self.subject
        msg["Message-ID"] = make_msgid(domain=sender.rsplit("@", 1)[-1] if "@" in sender else None)
        msg.set_content(self.text)
        return msg


__all__ = ["MAX_FIELD", "Mail", "clean_field"]
