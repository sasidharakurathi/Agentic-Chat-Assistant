from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import TZDateTime

if TYPE_CHECKING:
    from app.models.membership import Membership


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    #: None for an account made through Google sign-in (Phase 8.3): it has
    #: no password, so a password sign-in can't open it.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    #: The address was shown to reach them (Phase 7b.6): a link sent to it
    #: was opened, or they joined through an invite to it. Identity for
    #: data access (Phase 9) will need it.
    email_verified_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    #: The password last changed or reset. A session token issued before it
    #: no longer works.
    password_changed_at: Mapped[datetime | None] = mapped_column(TZDateTime())

    memberships: Mapped[list[Membership]] = relationship(
        back_populates="user", cascade="all, delete-orphan", foreign_keys="Membership.user_id"
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<User {self.email!r}>"
