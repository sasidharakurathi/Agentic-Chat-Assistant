from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

GroupBy = Literal["assistant", "model"]


class UsageRollupRow(BaseModel):
    group_key: str | None
    event_count: int
    tokens_in: int
    tokens_out: int
    cost_usd: Decimal


class UsageRollupResponse(BaseModel):
    group_by: GroupBy
    rows: list[UsageRollupRow]


__all__ = ["GroupBy", "UsageRollupResponse", "UsageRollupRow"]
