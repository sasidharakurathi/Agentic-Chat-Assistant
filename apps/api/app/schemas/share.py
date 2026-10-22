"""Read-only conversation links (Phase 9.4)."""

from __future__ import annotations

import uuid
from datetime import datetime

from app.schemas.common import ApiModel, ORMModel
from app.schemas.conversation import MessageOut


class ShareCreated(ApiModel):
    id: uuid.UUID
    #: Shown once: only a hash of it is kept.
    url: str
    created_at: datetime


class ShareOut(ORMModel):
    id: uuid.UUID
    created_at: datetime
    revoked_at: datetime | None
    last_opened_at: datetime | None
    open_count: int


class SharedConversationOut(ApiModel):
    title: str | None
    assistant_id: uuid.UUID
    assistant_name: str
    #: Who shared it.
    shared_by: str
    shared_at: datetime
    messages: list[MessageOut]


__all__ = ["ShareCreated", "ShareOut", "SharedConversationOut"]
