from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import TZDateTime

if TYPE_CHECKING:
    from app.models.organization import Organization


class ApiToken(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Scoped, hashed, revocable token for calling published assistants over the API.

    ``assistant_id`` is a plain nullable column for now — the assistants table
    arrives in Phase 1, at which point an FK is added by migration.
    """

    __tablename__ = "api_tokens"

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    prefix: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    scopes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    assistant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    expires_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    revoked_at: Mapped[datetime | None] = mapped_column(TZDateTime())

    organization: Mapped[Organization] = relationship()
