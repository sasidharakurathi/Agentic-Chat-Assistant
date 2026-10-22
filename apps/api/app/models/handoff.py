"""Hand-offs to a person (Phase 9.5).

One row each time a conversation was handed to a person: by the person in
it ("Get help from a person"), or by the assistant when it couldn't help.
The summary is built from the conversation without a model call and stays
here, behind sign-in: the email that goes out only links to the
conversation. When a declared HTTP action opened a ticket, `ticket_ref` is
the start of what the system answered.
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin
from app.db.types import TZDateTime

#: The most of a system's answer kept as the ticket reference.
TICKET_REF_MAX = 200


class HandoffStatus(enum.StrEnum):
    sent = "sent"
    failed = "failed"


class HandoffChannel(enum.StrEnum):
    #: The contact was emailed a link to the conversation.
    email = "email"
    #: A declared HTTP action opened a ticket.
    action = "action"


class Handoff(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "handoffs"

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    assistant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assistants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[HandoffStatus] = mapped_column(
        SAEnum(HandoffStatus, name="handoff_status", native_enum=False, length=20),
        nullable=False,
    )
    channel: Mapped[HandoffChannel] = mapped_column(
        SAEnum(HandoffChannel, name="handoff_channel", native_enum=False, length=20),
        nullable=False,
    )
    ticket_ref: Mapped[str | None] = mapped_column(String(TICKET_REF_MAX))
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime(),
        # From Python, for sub-second order: SQLite's now() is one-second.
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        nullable=False,
        index=True,
    )


__all__ = ["TICKET_REF_MAX", "Handoff", "HandoffChannel", "HandoffStatus"]
