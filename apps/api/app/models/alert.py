"""When Chat stops for everyone, an admin hears about it (Phase 8.9).

Prometheus is optional and pages nobody, so the app tells its own admins:
one open alert per kind of problem (and subject, such as one assistant),
counted each time it happens, emailed at most once an hour, shown as a
banner until it is resolved, by the next answer that works or by an admin.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin
from app.db.types import TZDateTime


class AdminAlert(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "admin_alerts"

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: What went wrong (`services.alerts.KINDS`).
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    #: What it is about, when it is one thing: an assistant's id.
    subject: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    detail: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    first_at: Mapped[datetime] = mapped_column(
        TZDateTime(), server_default=func.now(), nullable=False
    )
    last_at: Mapped[datetime] = mapped_column(
        TZDateTime(), server_default=func.now(), nullable=False
    )
    emailed_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    resolved_at: Mapped[datetime | None] = mapped_column(TZDateTime(), index=True)
    #: Who marked it handled; null when an answer that worked resolved it.
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


__all__ = ["AdminAlert"]
