"""Spend caps for an org or one assistant, per UTC day or month (task 5.7).

Only the limit is stored. What has been spent is always summed from the
`usage_events` ledger for the current period (`services/budgets.py`), so
there is no counter to drift, reset or forget: every paid call already
lands on the ledger, whoever made it (a turn, an AI helper, a summary, an
ingestion).

`scope_key` is "org" or the assistant's id: one value that a unique
constraint can hold (a NULL `assistant_id` would not be unique in
Postgres). `assistant_id` is there too, so deleting the assistant deletes
its budgets.
"""

from __future__ import annotations

import enum
import uuid
from decimal import Decimal

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class BudgetPeriod(enum.StrEnum):
    day = "day"
    month = "month"


class Budget(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "budgets"
    __table_args__ = (
        UniqueConstraint("org_id", "scope_key", "period", name="uq_budget_scope_period"),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    assistant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("assistants.id", ondelete="CASCADE"), nullable=True, index=True
    )
    #: "org", or the assistant's id.
    scope_key: Mapped[str] = mapped_column(String(40), nullable=False)
    period: Mapped[BudgetPeriod] = mapped_column(
        SAEnum(BudgetPeriod, name="budget_period", native_enum=False, length=10), nullable=False
    )
    limit_usd: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


__all__ = ["Budget", "BudgetPeriod"]
