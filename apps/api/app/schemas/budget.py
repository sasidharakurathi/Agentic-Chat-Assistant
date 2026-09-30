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


class BudgetsOut(ApiModel):
    budgets: list[BudgetStatusOut]
    #: Whether the caller may change these (admins and owners).
    can_edit: bool


__all__ = ["BudgetLimits", "BudgetStatusOut", "BudgetsOut"]
