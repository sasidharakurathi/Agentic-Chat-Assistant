from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import JSONB, TZDateTime


class ConversationStatus(enum.StrEnum):
    active = "active"
    archived = "archived"


class MessageRole(enum.StrEnum):
    user = "user"
    assistant = "assistant"
    system = "system"
    tool = "tool"


class RunStatus(enum.StrEnum):
    ok = "ok"
    error = "error"
    aborted = "aborted"


class Conversation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "conversations"

    assistant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assistants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Null = ran against the assistant's draft (unpublished).
    assistant_version_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="New conversation")
    sdk_session_id: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[ConversationStatus] = mapped_column(
        SAEnum(ConversationStatus, name="conversation_status", native_enum=False, length=20),
        nullable=False,
        default=ConversationStatus.active,
    )
    cost_usd: Mapped[float] = mapped_column(Numeric(12, 6), nullable=False, default=0)
    token_usage: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    last_message_at: Mapped[datetime | None] = mapped_column(TZDateTime())


class Message(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "messages"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[MessageRole] = mapped_column(
        SAEnum(MessageRole, name="message_role", native_enum=False, length=20), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    blocks: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    model: Mapped[str | None] = mapped_column(String(80))
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    # Python-side default for sub-second precision (SQLite's now() is 1s-coarse),
    # so messages in one turn keep a deterministic order.
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime(),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        nullable=False,
        index=True,
    )


class Run(UUIDPrimaryKeyMixin, Base):
    """One agent turn (one user message -> one assistant answer)."""

    __tablename__ = "runs"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    model: Mapped[str | None] = mapped_column(String(80))
    effort: Mapped[str | None] = mapped_column(String(20))
    driver: Mapped[str | None] = mapped_column(String(20))
    num_turns: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Numeric(12, 6), nullable=False, default=0)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[RunStatus] = mapped_column(
        SAEnum(RunStatus, name="run_status", native_enum=False, length=20),
        nullable=False,
        default=RunStatus.ok,
    )
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime(), server_default=func.now(), nullable=False
    )
