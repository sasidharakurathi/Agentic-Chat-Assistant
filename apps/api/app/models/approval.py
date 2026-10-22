"""Requests that wait for a person (plan §2.4 / §4.4; Phase 9.1, plan D17).

A row is created the moment a tool call needs a person. Since Phase 9.1 it
is the request itself, not a note about a wait: the turn that raised it has
ended, the exact input is sealed on the row, and the executor runs it once
after someone decided:

    pending ──approve──▶ approved ──claim──▶ executing ──▶ succeeded | failed
       │
       ├──decline──▶ denied      ├──expiry──▶ expired
       └──withdraw──▶ cancelled  (also: the asker left, the assistant paused)

Every move is one conditional UPDATE from the state before it, so two
deciders, a retried job or a sweep can never move it twice.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Index, LargeBinary, String, Text, UniqueConstraint, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin
from app.db.types import JSONB, TZDateTime

#: The sealing kind for a request's exact input (`security/crypto.seal`).
SEALED_KIND = "action_input"


class ApprovalStatus(enum.StrEnum):
    pending = "pending"
    #: decided yes; the executor has not claimed it yet.
    approved = "approved"
    denied = "denied"
    #: nobody answered in time. An unanswered request is never an implicit yes.
    expired = "expired"
    #: withdrawn by the asker, or ended because it can no longer run (the
    #: asker left, or before Phase 9.1, its turn stopped). Nothing ran.
    cancelled = "cancelled"
    #: claimed by the executor, running now (Phase 9.1).
    executing = "executing"
    succeeded = "succeeded"
    failed = "failed"


#: States a request can't leave.
FINAL = frozenset(
    {
        ApprovalStatus.denied,
        ApprovalStatus.expired,
        ApprovalStatus.cancelled,
        ApprovalStatus.succeeded,
        ApprovalStatus.failed,
    }
)
#: Still to happen: a conversation holding one of these isn't purged.
OPEN = frozenset({ApprovalStatus.pending, ApprovalStatus.approved, ApprovalStatus.executing})


class ApprovalRisk(enum.StrEnum):
    low = "low"
    medium = "medium"
    high = "high"


class ApprovalTier(enum.StrEnum):
    """Who decides (Phase 9.1, plan D7)."""

    #: the person asking: a declared action on their own rows, or a
    #: builder's test write in a preview.
    asker = "asker"
    #: one of the assistant's approvers (admins by default), never the asker.
    approver = "approver"


class Approval(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "approvals"
    __table_args__ = (Index("ix_approvals_status_expires", "status", "expires_at"),)

    #: SET NULL since Phase 9.1: removing a conversation (retention, an
    #: erasure) must not erase the record of a write that ran.
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), index=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: The assistant and version that asked (Phase 9.1): the executor runs it
    #: with that version's tools, and re-checks it is still in service.
    assistant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("assistants.id", ondelete="SET NULL"), index=True
    )
    #: None for a preview of the draft.
    assistant_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("assistant_versions.id", ondelete="SET NULL")
    )
    #: Raised in a builder's preview, where the builder decides (your choice).
    preview: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    tool_name: Mapped[str] = mapped_column(String(200), nullable=False)
    #: The declared action's name, when it is one (Phase 9.1).
    action_name: Mapped[str | None] = mapped_column(String(40))
    #: What people read on the card: the action's title, or the tool in words.
    title: Mapped[str | None] = mapped_column(String(200))
    #: The SDK's `tool_use_id` for the call this approval gates (plan §2.5),
    #: so the approval and the tool call in the trace can be joined.
    tool_call_id: Mapped[str | None] = mapped_column(String(100), index=True)
    #: The tool input as the model produced it, redacted: what people see.
    input: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    risk: Mapped[ApprovalRisk] = mapped_column(
        SAEnum(ApprovalRisk, name="approval_risk", native_enum=False, length=10),
        nullable=False,
        default=ApprovalRisk.medium,
    )
    tier: Mapped[ApprovalTier] = mapped_column(
        SAEnum(ApprovalTier, name="approval_tier", native_enum=False, length=10),
        nullable=False,
        default=ApprovalTier.approver,
        server_default="approver",
    )
    #: What would run, redacted: for SQL the statement (credentials masked).
    rationale: Mapped[str | None] = mapped_column(Text)
    status: Mapped[ApprovalStatus] = mapped_column(
        SAEnum(ApprovalStatus, name="approval_status", native_enum=False, length=20),
        nullable=False,
        default=ApprovalStatus.pending,
        index=True,
    )
    #: The exact input that runs, sealed under the KEK (Phase 9.1): it may
    #: hold values the redacted copy hides. Wiped once the request is final.
    sealed_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    sealed_dek: Mapped[bytes | None] = mapped_column(LargeBinary)
    sealed_nonce: Mapped[bytes | None] = mapped_column(LargeBinary)
    #: sha256 of the sealed input (canonical JSON). A decision must name it,
    #: so what is approved is what was shown; it stays in the audit log.
    input_sha256: Mapped[str | None] = mapped_column(String(64))
    #: Who asked: the person whose conversation raised it (Phase 7b.3). Kept
    #: on the row, so the record says who asked even when the conversation
    #: is gone, and whether they decided their own request.
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    #: The decider's note (required to decline, optional to approve).
    note: Mapped[str | None] = mapped_column(String(1000))
    expires_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    #: When the approvers were reminded (once).
    reminded_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    executed_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    #: What it did, or why it didn't (redacted, cut to a few thousand chars).
    result: Mapped[str | None] = mapped_column(Text)
    #: When the conversation's next turn was told how it ended.
    reported_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime(), server_default=func.now(), nullable=False
    )


class ApprovalDelegation(UUIDPrimaryKeyMixin, Base):
    """An approver hands their requests to someone until a date (Phase 9.1:
    out of office). The delegate decides what the approver could as a named
    approver; an admin's own powers never pass on."""

    __tablename__ = "approval_delegations"

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    delegate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    until: Mapped[datetime] = mapped_column(TZDateTime(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime(), server_default=func.now(), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(TZDateTime())


class ApproverNotice(UUIDPrimaryKeyMixin, Base):
    """When an approver was last emailed about requests waiting for them,
    and since when more have waited untold (Phase 9.2). The first request
    is emailed at once; more within `notify.BATCH_WINDOW` go out together
    as one "N requests wait" email."""

    __tablename__ = "approver_notices"
    __table_args__ = (UniqueConstraint("org_id", "user_id", name="uq_approver_notices_person"),)

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    last_sent_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    #: Set when a request waited and the person wasn't emailed about it.
    pending_since: Mapped[datetime | None] = mapped_column(TZDateTime(), index=True)


__all__ = [
    "FINAL",
    "OPEN",
    "SEALED_KIND",
    "Approval",
    "ApprovalDelegation",
    "ApprovalRisk",
    "ApprovalStatus",
    "ApprovalTier",
    "ApproverNotice",
]
