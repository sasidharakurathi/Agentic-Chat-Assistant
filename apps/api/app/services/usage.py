"""Org-level usage rollups: SUM/COUNT over ``usage_events``, grouped by
assistant, model or conversation. Backs ``GET /orgs/{id}/usage`` and the
usage dashboard (task 5.7).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assistant import Assistant
from app.models.conversation import Conversation
from app.models.usage import UsageEvent
from app.schemas.usage import GroupBy, UsageRollupRow


async def rollup(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    group_by: GroupBy,
    date_from: datetime | None,
    date_to: datetime | None,
    limit: int | None = None,
) -> list[UsageRollupRow]:
    group_col: Any
    label: Any = None
    if group_by == "assistant":
        group_col, label = UsageEvent.assistant_id, Assistant.name
    elif group_by == "conversation":
        group_col, label = UsageEvent.conversation_id, Conversation.title
    else:
        group_col = UsageEvent.model
    cols = [
        group_col.label("group_key"),
        func.count().label("event_count"),
        func.sum(UsageEvent.tokens_in).label("tokens_in"),
        func.sum(UsageEvent.tokens_out).label("tokens_out"),
        func.sum(UsageEvent.cost_usd).label("cost_usd"),
    ]
    if label is not None:
        cols.append(func.max(label).label("label"))
    if group_by == "conversation":
        # Titles repeat ("New conversation"); whose conversation it was tells
        # them apart.
        cols.append(func.max(Assistant.name).label("detail"))
    stmt = select(*cols).where(UsageEvent.org_id == org_id)
    # The name or title, for people; a deleted one has none (the ledger
    # keeps its spend, with the reference cleared).
    if group_by == "assistant":
        stmt = stmt.outerjoin(Assistant, Assistant.id == UsageEvent.assistant_id)
    elif group_by == "conversation":
        stmt = stmt.outerjoin(
            Conversation, Conversation.id == UsageEvent.conversation_id
        ).outerjoin(Assistant, Assistant.id == Conversation.assistant_id)
    stmt = stmt.group_by(group_col).order_by(func.sum(UsageEvent.cost_usd).desc())
    if date_from is not None:
        stmt = stmt.where(UsageEvent.created_at >= date_from)
    if date_to is not None:
        stmt = stmt.where(UsageEvent.created_at <= date_to)
    if limit is not None:
        stmt = stmt.limit(limit)

    rows = (await session.execute(stmt)).all()
    return [
        UsageRollupRow(
            group_key=str(r.group_key) if r.group_key is not None else None,
            label=getattr(r, "label", None) if label is not None else r.group_key,
            detail=getattr(r, "detail", None),
            event_count=r.event_count,
            tokens_in=int(r.tokens_in or 0),
            tokens_out=int(r.tokens_out or 0),
            cost_usd=r.cost_usd or 0,
        )
        for r in rows
    ]


__all__ = ["rollup"]
