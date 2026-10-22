from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin
from app.db.types import TZDateTime


class AuditLog(UUIDPrimaryKeyMixin, Base):
    """Append-only record of security-relevant actions. No ``updated_at``.

    Tamper-evident (Phase 7b.7): a sealer (`services.audit_chain`) gives each
    row, in turn, a sequence number and a hash over its contents and the
    previous row's hash. Changing or removing a sealed row breaks every hash
    after it. The append-only trigger allows exactly that one write: setting
    the seal on a row that has none."""

    __tablename__ = "audit_log"

    org_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="SET NULL"), index=True
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    action: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    target_type: Mapped[str | None] = mapped_column(String(80))
    target_id: Mapped[str | None] = mapped_column(String(80))
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    ip: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime(), server_default=func.now(), nullable=False, index=True
    )
    #: Who acted, as they were then (their email): kept when the account
    #: goes and `actor_user_id` is cleared (Phase 7b.7).
    actor_label: Mapped[str | None] = mapped_column(String(320))
    # ── the seal ──
    seq: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    prev_hash: Mapped[str | None] = mapped_column(String(64))
    row_hash: Mapped[str | None] = mapped_column(String(64))
