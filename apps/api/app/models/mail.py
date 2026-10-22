from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin
from app.db.types import TZDateTime


class MailStatus(enum.StrEnum):
    queued = "queued"
    sent = "sent"
    #: Given up: refused for good, out of attempts, or never reached the queue.
    failed = "failed"


class MailMessage(UUIDPrimaryKeyMixin, Base):
    """A message the platform sent, or tried to (Phase 7b.5).

    Who it went to, what kind and its subject, never its body: an invite's
    body holds a live link, which is stored only as a hash. The body travels
    in the queued job and is gone once sent."""

    __tablename__ = "mail_messages"

    org_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    to_address: Mapped[str] = mapped_column(String(320), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    subject: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[MailStatus] = mapped_column(
        SAEnum(MailStatus, name="mail_status", native_enum=False, length=10),
        nullable=False,
        default=MailStatus.queued,
        index=True,
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime(), server_default=func.now(), nullable=False
    )
    sent_at: Mapped[datetime | None] = mapped_column(TZDateTime())
