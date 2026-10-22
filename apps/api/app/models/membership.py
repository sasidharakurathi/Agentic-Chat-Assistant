from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import TZDateTime
from app.models.enums import MemberRole

if TYPE_CHECKING:
    from app.models.organization import Organization
    from app.models.user import User


class Membership(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("org_id", "user_id", name="uq_memberships_org_user"),)

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Two foreign keys to users now (`deactivated_by` too): the relationship
    # below says which one it follows.
    role: Mapped[MemberRole] = mapped_column(
        SAEnum(MemberRole, name="member_role", native_enum=False, length=20),
        nullable=False,
        default=MemberRole.member,
    )
    #: Removed from the org by an admin (Phase 7b.4). The row stays, so who
    #: they were and what they did keep their names; while set, the person
    #: reaches nothing in the org. Reactivating clears it.
    deactivated_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    deactivated_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    @property
    def active(self) -> bool:
        return self.deactivated_at is None

    organization: Mapped[Organization] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship(back_populates="memberships", foreign_keys=[user_id])

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<Membership org={self.org_id} user={self.user_id} role={self.role.value}>"
