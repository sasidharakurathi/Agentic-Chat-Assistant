from __future__ import annotations

from decimal import Decimal
from typing import Literal

from app.schemas.common import ApiModel

GroupBy = Literal["assistant", "model", "conversation"]


class UsageRollupRow(ApiModel):
    group_key: str | None
    #: The assistant's name, the conversation's title, or the model id; None
    #: when the assistant or conversation has been deleted.
    label: str | None = None
    #: For a conversation, its assistant's name.
    detail: str | None = None
    event_count: int
    tokens_in: int
    tokens_out: int
    cost_usd: Decimal


class UsageRollupResponse(ApiModel):
    group_by: GroupBy
    rows: list[UsageRollupRow]


__all__ = ["GroupBy", "UsageRollupResponse", "UsageRollupRow"]
