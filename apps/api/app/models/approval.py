"""Human-in-the-loop approvals (plan §2.4 / §4.4).

A row is created the moment a tool call needs a human, and resolved by
`POST /approvals/{id}:resolve`. It is deliberately **durable** rather than
purely in-memory: the in-process registry that a waiting `can_use_tool`
blocks on is a fast path, but the row is what lets the UI re-render a pending
approval after a refresh, and what makes "who approved this DELETE, and when"
answerable later.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin
from app.db.types import JSONB, TZDateTime


class ApprovalStatus(enum.StrEnum):
    pending = "pending"
    approved = "approved"
    denied = "denied"
    #: nobody answered in time. Treated exactly like `denied` at the call
    #: site — an unattended approval must never become an implicit yes.
    expired = "expired"


class ApprovalRisk(enum.StrEnum):
    low = "low"
    medium = "medium"
    high = "high"


class Approval(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "approvals"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tool_name: Mapped[str] = mapped_column(String(200), nullable=False)
    #: The SDK's `tool_use_id` for the call this approval gates (plan §2.5),
    #: so the approval and the tool call in the trace can be joined.
    tool_call_id: Mapped[str | None] = mapped_column(String(100), index=True)
    #: The tool input as the model produced it, redacted before it is ever
    #: sent to a client. This is what the reviewer is actually approving.
    input: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    risk: Mapped[ApprovalRisk] = mapped_column(
        SAEnum(ApprovalRisk, name="approval_risk", native_enum=False, length=10),
        nullable=False,
        default=ApprovalRisk.medium,
    )
    rationale: Mapped[str | None] = mapped_column(Text)
    status: Mapped[ApprovalStatus] = mapped_column(
        SAEnum(ApprovalStatus, name="approval_status", native_enum=False, length=20),
        nullable=False,
        default=ApprovalStatus.pending,
        index=True,
    )
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    expires_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime(), server_default=func.now(), nullable=False
    )


__all__ = ["Approval", "ApprovalRisk", "ApprovalStatus"]
