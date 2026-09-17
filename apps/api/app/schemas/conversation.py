from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from app.models.conversation import ConversationStatus, MessageRole
from app.schemas.common import ORMModel


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class ConversationRename(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=32_000)


class ConversationSummary(ORMModel):
    id: uuid.UUID
    assistant_id: uuid.UUID
    assistant_version_id: uuid.UUID | None
    title: str
    status: ConversationStatus
    cost_usd: Decimal
    token_usage: dict[str, Any]
    created_at: datetime
    last_message_at: datetime | None


class MessageOut(ORMModel):
    id: uuid.UUID
    role: MessageRole
    content: str
    blocks: list[Any]
    model: str | None
    tokens_in: int
    tokens_out: int
    latency_ms: int | None
    created_at: datetime


class ConversationDetail(ConversationSummary):
    messages: list[MessageOut]


__all__ = [
    "ConversationCreate",
    "ConversationDetail",
    "ConversationRename",
    "ConversationSummary",
    "MessageIn",
    "MessageOut",
]
