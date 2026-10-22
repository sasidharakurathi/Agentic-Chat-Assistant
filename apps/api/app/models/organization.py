from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import TZDateTime

if TYPE_CHECKING:
    from app.models.membership import Membership


class Organization(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), nullable=False, unique=True, index=True)
    is_personal: Mapped[bool] = mapped_column(default=False, nullable=False)
    #: Set aside (Phase 7b.8): nobody reaches it, and nothing in it is
    #: deleted. The upgrade archives empty personal orgs; the operator's
    #: `scripts.upgrade_report` archives others, and brings them back.
    archived_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    archived_reason: Mapped[str | None] = mapped_column(String(200))
    #: The owner's switches (`services.org_settings`). A key not stored has
    #: its default, so a new switch needs no migration.
    settings: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default="{}"
    )
    #: Bumped whenever someone may read less (Phase 9.4): a group member
    #: removed, a group deleted, a source's readers changed. A conversation
    #: on restricted knowledge last answered at an older epoch starts fresh.
    access_epoch: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    memberships: Mapped[list[Membership]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<Organization {self.slug!r}>"
