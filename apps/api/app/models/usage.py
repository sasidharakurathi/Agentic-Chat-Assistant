"""Usage ledger: one row per billable event (an LLM turn today; embedding/rerank/
tool calls from Phase 2 onward). Distinct from ``runs`` (one row per conversation
turn, keyed for tracing) — this is the flat, append-only log the org/assistant
usage rollup queries (``GET /orgs/{id}/usage``) group and sum over.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Index, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin
from app.db.types import TZDateTime


class UsageKind(enum.StrEnum):
    llm = "llm"
    embedding = "embedding"
    rerank = "rerank"
    tool = "tool"


class UsageEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "usage_events"
    #: Budgets sum an org's, or one assistant's, spend since the start of the
    #: day or month on every turn (task 5.7).
    __table_args__ = (
        Index("ix_usage_events_org_created", "org_id", "created_at"),
        Index("ix_usage_events_assistant_created", "assistant_id", "created_at"),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # SET NULL, not CASCADE: this is an append-only ledger of spend that really
    # happened. Deleting an assistant or a conversation must not make an
    # org's usage totals drop.
    assistant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("assistants.id", ondelete="SET NULL"), nullable=True, index=True
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    kind: Mapped[UsageKind] = mapped_column(
        SAEnum(UsageKind, name="usage_kind", native_enum=False, length=20), nullable=False
    )
    model: Mapped[str | None] = mapped_column(String(80))
    #: For kind=tool, the number of calls (a tool has no tokens).
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Non-token usage (e.g. chunks embedded, documents reranked) — null for "llm".
    units: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[float] = mapped_column(Numeric(12, 6), nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime(), server_default=func.now(), nullable=False, index=True
    )


__all__ = ["UsageEvent", "UsageKind"]
