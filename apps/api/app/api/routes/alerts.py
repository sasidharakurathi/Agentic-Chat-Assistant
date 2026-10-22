"""What stopped Chat for everyone, for the admins (Phase 8.9): the banner's
source, the alerts page, and marking a problem handled."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import ClientIP, SessionDep, require_role
from app.models.assistant import Assistant
from app.models.enums import MemberRole
from app.models.membership import Membership
from app.schemas.common import ApiModel
from app.services import alerts

router = APIRouter(tags=["alerts"])

RequireAdmin = Depends(require_role(MemberRole.admin))


class AlertOut(ApiModel):
    kind: str
    #: The problem in words.
    what: str
    #: An assistant's id, for its budget; empty otherwise.
    subject: str
    #: That assistant's name.
    subject_name: str | None = None
    detail: str
    count: int
    first_at: datetime
    last_at: datetime
    emailed_at: datetime | None = None
    resolved_at: datetime | None = None


class AlertResolve(BaseModel):
    kind: str = Field(min_length=1, max_length=40)
    subject: str = Field(default="", max_length=80)


class AlertResolved(ApiModel):
    resolved: int


@router.get("/orgs/{org_id}/alerts", response_model=list[AlertOut])
async def list_alerts(
    org_id: Annotated[uuid.UUID, Path()],
    _actor: Annotated[Membership, RequireAdmin],
    session: SessionDep,
    open: bool = False,
) -> list[AlertOut]:
    """The org's alerts, newest first: the open ones (`open=true`, the
    banner's), or the last hundred."""
    rows = await alerts.listed(session, org_id, open_only=open)
    ids = [uuid.UUID(r.subject) for r in rows if r.kind == "budget_assistant" and r.subject]
    names = (
        dict(
            (
                await session.execute(
                    select(Assistant.id, Assistant.name).where(Assistant.id.in_(ids))
                )
            )
            .tuples()
            .all()
        )
        if ids
        else {}
    )
    return [
        AlertOut(
            kind=r.kind,
            what=alerts.KINDS.get(r.kind, r.kind),
            subject=r.subject,
            subject_name=names.get(uuid.UUID(r.subject))
            if r.kind == "budget_assistant" and r.subject
            else None,
            detail=r.detail,
            count=r.count,
            first_at=r.first_at,
            last_at=r.last_at,
            emailed_at=r.emailed_at,
            resolved_at=r.resolved_at,
        )
        for r in rows
    ]


@router.post("/orgs/{org_id}/alerts:resolve", response_model=AlertResolved)
async def resolve_alert(
    org_id: Annotated[uuid.UUID, Path()],
    body: AlertResolve,
    actor: Annotated[Membership, RequireAdmin],
    session: SessionDep,
    ip: ClientIP,
) -> AlertResolved:
    """Mark a problem handled: its banner goes. Audited."""
    n = await alerts.resolve(
        session, org_id=org_id, kind=body.kind, subject=body.subject, actor_id=actor.user_id, ip=ip
    )
    return AlertResolved(resolved=n)


__all__ = ["router"]
