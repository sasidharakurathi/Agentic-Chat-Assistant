"""Screenshots in chat (Phase 9.6): what an upload returns."""

from __future__ import annotations

import uuid
from datetime import datetime

from app.schemas.common import ORMModel


class AttachmentOut(ORMModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    #: Empty until the attachment is sent with a message.
    message_id: uuid.UUID | None = None
    content_type: str
    bytes: int
    created_at: datetime


__all__ = ["AttachmentOut"]
