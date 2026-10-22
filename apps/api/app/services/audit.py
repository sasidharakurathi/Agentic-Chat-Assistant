from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.user import User


async def record(
    session: AsyncSession,
    *,
    action: str,
    org_id: uuid.UUID | None = None,
    actor_user_id: uuid.UUID | None = None,
    target_type: str | None = None,
    target_id: str | uuid.UUID | None = None,
    meta: dict[str, Any] | None = None,
    ip: str | None = None,
) -> AuditLog:
    # Their email as it is now: the record keeps naming them after the account
    # is deleted or the id is cleared (Phase 7b.7).
    actor = await session.get(User, actor_user_id) if actor_user_id is not None else None
    entry = AuditLog(
        org_id=org_id,
        actor_user_id=actor_user_id,
        actor_label=actor.email if actor is not None else None,
        action=action,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        meta=meta or {},
        ip=ip,
    )
    session.add(entry)
    await session.flush()
    return entry


__all__ = ["record"]
