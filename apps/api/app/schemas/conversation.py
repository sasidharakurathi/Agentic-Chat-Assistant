from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from app.models.conversation import ConversationStatus, MessageRole, RunStatus
from app.schemas.common import ApiModel, ORMModel


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    external_user_ref: str | None = Field(default=None, max_length=200)


class ConversationRename(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=32_000)


class ConversationSummary(ORMModel):
    id: uuid.UUID
    assistant_id: uuid.UUID
    assistant_version_id: uuid.UUID | None
    title: str
    external_user_ref: str | None = None
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


class RunOut(ORMModel):
    """One agent turn, as recorded: what it cost, how long it took, how it
    ended, and the trace id that finds it in the logs / tracing backend."""

    id: uuid.UUID
    conversation_id: uuid.UUID
    message_id: uuid.UUID | None
    trace_id: str | None
    #: The published version that answered; null when the draft did.
    version_number: int | None
    model: str | None
    effort: str | None
    driver: str | None
    num_turns: int
    tokens_in: int
    tokens_out: int
    cost_usd: Decimal
    duration_ms: int | None
    status: RunStatus
    error: str | None
    created_at: datetime


class RunStep(ApiModel):
    """A tool call the turn made, in order."""

    id: str | None = None
    name: str
    input: Any = None
    status: str | None = None
    output: str | None = None


class RunDetail(RunOut):
    steps: list[RunStep]


class ConversationDetail(ConversationSummary):
    #: The most recent messages, oldest-first. Not the whole history.
    messages: list[MessageOut]
    #: Pass to `GET /conversations/{id}/messages?cursor=` for older messages;
    #: null when `messages` is everything.
    messages_next_cursor: str | None = None


__all__ = [
    "ConversationCreate",
    "ConversationDetail",
    "ConversationRename",
    "ConversationSummary",
    "MessageIn",
    "MessageOut",
    "RunDetail",
    "RunOut",
    "RunStep",
]
