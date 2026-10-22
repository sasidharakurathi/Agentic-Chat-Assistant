"""Co-editors of an assistant (Phase 7b.4).

An assistant outlives the person who built it. Its creator or an admin can
name others who may edit it: they change it, preview it and submit it like
its creator (`access.can_edit_assistant`), but don't name co-editors
themselves. Each grant and withdrawal is audited. Deactivating someone
withdraws their grants (`people.deactivate`).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import BadRequest, Conflict, NotFound
from app.models.assistant import Assistant, AssistantEditor
from app.models.enums import MemberRole
from app.models.membership import Membership
from app.models.user import User
from app.services import audit


@dataclass(frozen=True)
class Editor:
    user_id: uuid.UUID
    email: str
    name: str
    granted_by: uuid.UUID | None
    created_at: datetime


async def list_for(session: AsyncSession, assistant: Assistant) -> list[Editor]:
    rows = await session.execute(
        select(AssistantEditor, User)
        .join(User, User.id == AssistantEditor.user_id)
        .where(AssistantEditor.assistant_id == assistant.id)
        .order_by(AssistantEditor.created_at)
    )
    return [Editor(e.user_id, u.email, u.name, e.granted_by, e.created_at) for e, u in rows.all()]


async def add(
    session: AsyncSession,
    assistant: Assistant,
    *,
    user_id: uuid.UUID,
    actor_id: uuid.UUID,
    ip: str | None = None,
) -> None:
    if user_id == assistant.created_by:
        raise BadRequest("They created it: they can already edit it", code="already_editor")
    member = await session.scalar(
        select(Membership)
        .join(User, User.id == Membership.user_id)
        .where(
            Membership.org_id == assistant.org_id,
            Membership.user_id == user_id,
            Membership.deactivated_at.is_(None),
            User.is_active.is_(True),
        )
    )
    if member is None:
        raise NotFound("That person isn't an active member of this organization")
    if not member.role.satisfies(MemberRole.member):
        raise BadRequest(
            "Employees chat; they don't build. Make them a member first",
            code="not_a_builder",
        )
    exists = await session.scalar(
        select(AssistantEditor.id).where(
            AssistantEditor.assistant_id == assistant.id, AssistantEditor.user_id == user_id
        )
    )
    if exists is not None:
        raise Conflict("They can already edit it", code="already_editor")
    session.add(
        AssistantEditor(
            assistant_id=assistant.id,
            user_id=user_id,
            org_id=assistant.org_id,
            granted_by=actor_id,
        )
    )
    await audit.record(
        session,
        action="assistant.editor.add",
        org_id=assistant.org_id,
        actor_user_id=actor_id,
        target_type="assistant",
        target_id=assistant.id,
        meta={"user_id": str(user_id)},
        ip=ip,
    )
    await session.commit()


async def remove(
    session: AsyncSession,
    assistant: Assistant,
    *,
    user_id: uuid.UUID,
    actor_id: uuid.UUID,
    ip: str | None = None,
) -> None:
    row = await session.scalar(
        select(AssistantEditor).where(
            AssistantEditor.assistant_id == assistant.id, AssistantEditor.user_id == user_id
        )
    )
    if row is None:
        raise NotFound("They aren't a co-editor of this assistant")
    await session.delete(row)
    await audit.record(
        session,
        action="assistant.editor.remove",
        org_id=assistant.org_id,
        actor_user_id=actor_id,
        target_type="assistant",
        target_id=assistant.id,
        meta={"user_id": str(user_id)},
        ip=ip,
    )
    await session.commit()


__all__ = ["Editor", "add", "list_for", "remove"]
