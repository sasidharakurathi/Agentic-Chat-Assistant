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
    #: The model declined to answer (task 5.4), even after any fallback.
    refused = "refused"


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
    #: An identifier from the embedding application for its own end user
    #: (plan §2.5), so it can list "this user's conversations" without keeping
    #: its own mapping. Opaque to us; never shown to the model.
    external_user_ref: Mapped[str | None] = mapped_column(String(200), index=True)
    sdk_session_id: Mapped[str | None] = mapped_column(String(128))
    # {"markers": {chunk_id: n}, "next": n} — the citation numbers already
    # handed out in this conversation. Lives here rather than being rebuilt
    # per turn because the SDK session is resumed: the model's context still
    # contains earlier turns' numbered kb_search results, so a marker has to
    # keep meaning the same chunk for as long as that context does.
    citation_state: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[ConversationStatus] = mapped_column(
        SAEnum(ConversationStatus, name="conversation_status", native_enum=False, length=20),
        nullable=False,
        default=ConversationStatus.active,
    )
    cost_usd: Mapped[float] = mapped_column(Numeric(12, 6), nullable=False, default=0)
    token_usage: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    last_message_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    # ── long conversations (task 5.2, `agent/history.py`) ──
    #: A rolling summary of every message up to `summary_through_at`.
    summary: Mapped[str | None] = mapped_column(Text)
    summary_through_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    #: Bumped each time the summary is rewritten.
    summary_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    #: The summary version the current SDK session was started from. When it
    #: is behind `summary_version`, the next turn starts a fresh session from
    #: the summary instead of resuming the long one.
    session_summary_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )


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
    #: Why the main loop stopped ("end_turn", "refusal", ...; task 5.4).
    stop_reason: Mapped[str | None] = mapped_column(String(40))
    #: Set when the fallback model answered instead of the main one.
    fallback_model: Mapped[str | None] = mapped_column(String(80))
    #: How the router sorted the message, when one is wired in (task 5.10):
    #: "simple", "normal" or "hard"; `effort` is what the turn ran at.
    route: Mapped[str | None] = mapped_column(String(20))
    status: Mapped[RunStatus] = mapped_column(
        SAEnum(RunStatus, name="run_status", native_enum=False, length=20),
        nullable=False,
        default=RunStatus.ok,
    )
    error: Mapped[str | None] = mapped_column(Text)
    #: The OpenTelemetry trace id when tracing is on, else a minted one in the
    #: same format. Also bound into every log line of the turn.
    trace_id: Mapped[str | None] = mapped_column(String(32), index=True)
    #: The published version that answered; None when the draft did.
    version_number: Mapped[int | None] = mapped_column(Integer)
    # Microseconds from Python, like Message: runs are listed newest-first,
    # and SQLite's CURRENT_TIMESTAMP (one-second resolution) left two turns in
    # the same second ordered by their random UUIDs.
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime(),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        nullable=False,
    )


class ToolCall(UUIDPrimaryKeyMixin, Base):
    """One tool call a turn made (plan §2.5, PostToolUse (c)).

    Tool calls also ride on the assistant message's `blocks` for the UI, but
    that is a JSON column shaped for rendering. This is the queryable record:
    which tools run, how often, how long they take and how often they fail,
    per org and per conversation. Inputs are redacted and outputs are the
    capped previews, as streamed.
    """

    __tablename__ = "tool_calls"

    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: The SDK's tool_use_id.
    call_id: Mapped[str | None] = mapped_column(String(100), index=True)
    tool_name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    #: "caps" for platform capabilities, "builtin" for SDK tools, else the MCP
    #: server's name.
    server: Mapped[str] = mapped_column(String(100), nullable=False)
    input: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    output: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(String(20))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    #: How the call came to run, or not (task 4.7): "auto" (no one needed
    #: asking), "approved", "declined", "expired", "interrupted" or
    #: "refused" (policy or credential). Null for tools that never ask.
    permission: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime(),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        nullable=False,
    )
