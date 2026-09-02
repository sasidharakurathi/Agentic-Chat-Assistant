from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status

from app.api.deps import ClientIP, CurrentUser, OrgMembership, SessionDep, require_role
from app.models.enums import MemberRole
from app.models.user import User
from app.schemas.common import Message
from app.schemas.org import (
    AuditEntryOut,
    InviteCreate,
    InviteOut,
    MemberOut,
    OrgCreate,
    OrgOut,
    RoleUpdate,
)
from app.services import orgs as orgs_service

router = APIRouter(tags=["orgs"])

RequireAdmin = Depends(require_role(MemberRole.admin))
RequireOwner = Depends(require_role(MemberRole.owner))


@router.get("/orgs", response_model=list[OrgOut])
async def list_orgs(user: CurrentUser, session: SessionDep) -> list[OrgOut]:
    orgs = await orgs_service.list_for_user(session, user.id)
    return [OrgOut.model_validate(o) for o in orgs]


@router.post("/orgs", response_model=OrgOut, status_code=status.HTTP_201_CREATED)
async def create_org(
    body: OrgCreate, user: CurrentUser, session: SessionDep, ip: ClientIP
) -> OrgOut:
    org = await orgs_service.create(session, user_id=user.id, name=body.name, ip=ip)
    return OrgOut.model_validate(org)


@router.get("/orgs/{org_id}/members", response_model=list[MemberOut])
async def list_members(
    org_id: Annotated[uuid.UUID, Path()],
    _membership: OrgMembership,
    session: SessionDep,
) -> list[MemberOut]:
    rows = await orgs_service.list_members(session, org_id)
    return [
        MemberOut(user_id=u.id, email=u.email, name=u.name, role=m.role, joined_at=m.created_at)
        for u, m in rows
    ]


@router.patch(
    "/orgs/{org_id}/members/{user_id}",
    response_model=MemberOut,
    dependencies=[RequireAdmin],
)
async def update_member_role(
    org_id: Annotated[uuid.UUID, Path()],
    user_id: Annotated[uuid.UUID, Path()],
    body: RoleUpdate,
    actor: CurrentUser,
    session: SessionDep,
    ip: ClientIP,
) -> MemberOut:
    membership = await orgs_service.change_role(
        session,
        org_id=org_id,
        actor_user_id=actor.id,
        target_user_id=user_id,
        new_role=body.role,
        ip=ip,
    )
    target = await session.get(User, user_id)
    assert target is not None  # change_role already verified membership
    return MemberOut(
        user_id=target.id,
        email=target.email,
        name=target.name,
        role=membership.role,
        joined_at=membership.created_at,
    )


@router.post(
    "/orgs/{org_id}/invites",
    response_model=InviteOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[RequireAdmin],
)
async def create_invite(
    org_id: Annotated[uuid.UUID, Path()],
    body: InviteCreate,
    actor: CurrentUser,
    session: SessionDep,
    ip: ClientIP,
) -> InviteOut:
    invite, raw = await orgs_service.create_invite(
        session,
        org_id=org_id,
        actor_user_id=actor.id,
        email=body.email,
        role=body.role,
        ip=ip,
    )
    return InviteOut(
        id=invite.id,
        email=invite.email,
        role=invite.role,
        expires_at=invite.expires_at,
        accept_url=orgs_service.invite_accept_url(raw),
    )


@router.post("/invites/{token}/accept", response_model=Message)
async def accept_invite(
    token: Annotated[str, Path()], user: CurrentUser, session: SessionDep, ip: ClientIP
) -> Message:
    await orgs_service.accept_invite(session, raw_token=token, user=user, ip=ip)
    return Message(message="joined organization")


@router.get(
    "/orgs/{org_id}/audit-log",
    response_model=list[AuditEntryOut],
    dependencies=[RequireAdmin],
)
async def list_audit_log(
    org_id: Annotated[uuid.UUID, Path()],
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[AuditEntryOut]:
    entries = await orgs_service.list_audit(session, org_id=org_id, limit=limit, offset=offset)
    return [AuditEntryOut.model_validate(e) for e in entries]
