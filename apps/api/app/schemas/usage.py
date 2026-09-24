from __future__ import annotations

from decimal import Decimal
from typing import Literal

from app.schemas.common import ApiModel

GroupBy = Literal["assistant", "model"]


class UsageRollupRow(ApiModel):
    group_key: str | None
    event_count: int
    tokens_in: int
    tokens_out: int
    cost_usd: Decimal


class UsageRollupResponse(ApiModel):
    group_by: GroupBy
    rows: list[UsageRollupRow]


__all__ = ["GroupBy", "UsageRollupResponse", "UsageRollupRow"]
