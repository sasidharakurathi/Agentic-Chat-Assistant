"""A screenshot attached to a chat message (Phase 9.6).

Uploaded first, into the conversation, then sent with a message: until
then `message_id` is empty, and once it is set the attachment is used and
cannot be sent again. The image itself is in object storage under
`object_key`; erasing the conversation removes the object too
(`services/retention.purge`).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin
from app.db.types import TZDateTime


class Attachment(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "attachments"

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    #: The message it was sent with; empty until then.
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), index=True
    )
    object_key: Mapped[str] = mapped_column(String(500), nullable=False)
    #: Decided from the bytes at upload, never the browser's claim.
    content_type: Mapped[str] = mapped_column(String(50), nullable=False)
    bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime(),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        nullable=False,
    )


__all__ = ["Attachment"]
