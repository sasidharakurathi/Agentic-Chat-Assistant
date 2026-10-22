from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field

from app.schemas.common import ApiModel

#: A limit, or none: at least a cent (limits are stored to four places),
#: and the upper bound only catches a typo'd extra zeros.
Limit = float | None


class BudgetLimits(ApiModel):
    """The day and month limits of one scope, in USD (task 5.7). `null`
    removes that limit."""

    daily_usd: Limit = Field(default=None, ge=0.01, le=1_000_000)
    monthly_usd: Limit = Field(default=None, ge=0.01, le=10_000_000)


class BudgetStatusOut(ApiModel):
    scope: Literal["org", "assistant"]
    assistant_id: uuid.UUID | None = None
    #: The assistant's name, for the dashboard.
    assistant_name: str | None = None
    period: Literal["day", "month"]
    limit_usd: float
    spent_usd: float
    #: spent / limit.
    ratio: float
    #: "warning" from 80%, "exceeded" at 100%.
    state: Literal["ok", "warning", "exceeded"]
    resets_at: datetime


class BuilderSpendOut(ApiModel):
    """Builder testing this month (Phase 9.7): previews and eval runs,
    org-wide. Not part of the budgets above, which are the pilot's."""

    spent_usd: float
    #: `builder_monthly_usd`, set by the owner; null: no cap.
    limit_usd: float | None = None
    #: spent / limit, when there is a limit.
    ratio: float | None = None
    state: Literal["ok", "warning", "exceeded"] = "ok"
    resets_at: datetime


class BudgetsOut(ApiModel):
    budgets: list[BudgetStatusOut]
    #: Whether the caller may change these (admins and owners).
    can_edit: bool
    #: The organisation's page only (Phase 9.7); null elsewhere.
    builder: BuilderSpendOut | None = None


class PersonDefaultIn(BudgetLimits):
    """The cap each person in a role gets (Phase 8.9). `builder` covers
    builders, admins and the owner."""

    role: Literal["employee", "builder"]


class PersonDefaultOut(ApiModel):
    role: Literal["employee", "builder"]
    daily_usd: float | None = None
    monthly_usd: float | None = None


class PersonBudgetOut(ApiModel):
    """One member's spend and the caps that apply to them."""

    user_id: uuid.UUID
    name: str
    email: str
    role: str
    spent_today_usd: float
    spent_month_usd: float
    daily_usd: float | None = None
    monthly_usd: float | None = None
    #: Their own override rather than their role's default.
    own_daily: bool = False
    own_monthly: bool = False


class PeopleBudgetsOut(ApiModel):
    defaults: list[PersonDefaultOut]
    people: list[PersonBudgetOut]


__all__ = [
    "BudgetLimits",
    "BudgetStatusOut",
    "BudgetsOut",
    "BuilderSpendOut",
    "PeopleBudgetsOut",
    "PersonBudgetOut",
    "PersonDefaultIn",
    "PersonDefaultOut",
]
