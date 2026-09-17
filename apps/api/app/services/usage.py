"""Org-level usage rollups: SUM/COUNT over ``usage_events``, grouped by
assistant or model. Backs ``GET /orgs/{id}/usage``.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.usage import UsageEvent
from app.schemas.usage import GroupBy, UsageRollupRow


async def rollup(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    group_by: GroupBy,
    date_from: datetime | None,
    date_to: datetime | None,
) -> list[UsageRollupRow]:
    group_col = UsageEvent.assistant_id if group_by == "assistant" else UsageEvent.model
    stmt = (
        select(
            group_col.label("group_key"),
            func.count().label("event_count"),
            func.sum(UsageEvent.tokens_in).label("tokens_in"),
            func.sum(UsageEvent.tokens_out).label("tokens_out"),
            func.sum(UsageEvent.cost_usd).label("cost_usd"),
        )
        .where(UsageEvent.org_id == org_id)
        .group_by(group_col)
        .order_by(func.sum(UsageEvent.cost_usd).desc())
    )
    if date_from is not None:
        stmt = stmt.where(UsageEvent.created_at >= date_from)
    if date_to is not None:
        stmt = stmt.where(UsageEvent.created_at <= date_to)

    rows = (await session.execute(stmt)).all()
    return [
        UsageRollupRow(
            group_key=str(r.group_key) if r.group_key is not None else None,
            event_count=r.event_count,
            tokens_in=int(r.tokens_in or 0),
            tokens_out=int(r.tokens_out or 0),
            cost_usd=r.cost_usd or 0,
        )
        for r in rows
    ]


__all__ = ["rollup"]
