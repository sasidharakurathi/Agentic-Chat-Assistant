from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, status

from app.api.deps import (
    ClientIP,
    CurrentUser,
    OrgMembership,
    PageQuery,
    SessionDep,
    require_role,
)
from app.models.enums import MemberRole
from app.models.user import User
from app.schemas.common import Page
from app.schemas.org import (
    AuditEntryOut,
    InviteAccepted,
    InviteCreate,
    InviteOut,
    InvitePreview,
    MemberOut,
    OrgCreate,
    OrgOut,
    RoleUpdate,
)
from app.schemas.usage import GroupBy, UsageRollupResponse
from app.services import orgs as orgs_service
from app.services import usage as usage_service

router = APIRouter(tags=["orgs"])

RequireAdmin = Depends(require_role(MemberRole.admin))
# There is deliberately no RequireOwner here any more. It was declared and
# applied to no route, which read as an enforced owner gate that did not
# exist. "Owner required" is only true when a change *touches* owner, which a
# route dependency cannot see — see `orgs_service._authorise_role_change`.


@router.get("/orgs", response_model=Page[OrgOut])
async def list_orgs(user: CurrentUser, session: SessionDep, page: PageQuery) -> Page[OrgOut]:
    orgs = await orgs_service.list_for_user(session, user.id, limit=page.limit, cursor=page.cursor)
    return Page(items=[OrgOut.model_validate(o) for o in orgs.items], next_cursor=orgs.next_cursor)


@router.post("/orgs", response_model=OrgOut, status_code=status.HTTP_201_CREATED)
async def create_org(
    body: OrgCreate, user: CurrentUser, session: SessionDep, ip: ClientIP
) -> OrgOut:
    org = await orgs_service.create(session, user_id=user.id, name=body.name, ip=ip)
    return OrgOut.model_validate(org)


@router.get("/orgs/{org_id}/members", response_model=Page[MemberOut])
async def list_members(
    org_id: Annotated[uuid.UUID, Path()],
    _membership: OrgMembership,
    session: SessionDep,
    page: PageQuery,
) -> Page[MemberOut]:
    rows = await orgs_service.list_members(session, org_id, limit=page.limit, cursor=page.cursor)
    return Page(
        items=[
            MemberOut(user_id=u.id, email=u.email, name=u.name, role=m.role, joined_at=m.created_at)
            for u, m in rows.items
        ],
        next_cursor=rows.next_cursor,
    )


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


@router.get("/invites/{token}", response_model=InvitePreview)
async def preview_invite(token: Annotated[str, Path()], session: SessionDep) -> InvitePreview:
    """Unauthenticated on purpose: the invitee may not have an account yet,
    and needs to know which email to sign in with. The token is 32 random
    bytes and single-use, so holding it is the authorisation."""
    invite, org = await orgs_service.preview_invite(session, raw_token=token)
    state: Literal["pending", "accepted", "expired"] = (
        "accepted"
        if invite.accepted_at is not None
        else "expired"
        if invite.expires_at <= datetime.now(UTC)
        else "pending"
    )
    return InvitePreview(
        org_name=org.name,
        email=invite.email,
        role=invite.role,
        expires_at=invite.expires_at,
        status=state,
    )


@router.post("/invites/{token}/accept", response_model=InviteAccepted)
async def accept_invite(
    token: Annotated[str, Path()], user: CurrentUser, session: SessionDep, ip: ClientIP
) -> InviteAccepted:
    membership = await orgs_service.accept_invite(session, raw_token=token, user=user, ip=ip)
    _, org = await orgs_service.preview_invite(session, raw_token=token)
    return InviteAccepted(org_id=org.id, org_name=org.name, role=membership.role)


@router.get("/orgs/{org_id}/usage", response_model=UsageRollupResponse)
async def get_usage(
    org_id: Annotated[uuid.UUID, Path()],
    _membership: OrgMembership,
    session: SessionDep,
    group_by: Annotated[GroupBy, Query()] = "assistant",
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: Annotated[datetime | None, Query()] = None,
) -> UsageRollupResponse:
    rows = await usage_service.rollup(
        session, org_id=org_id, group_by=group_by, date_from=from_, date_to=to
    )
    return UsageRollupResponse(group_by=group_by, rows=rows)


@router.get(
    "/orgs/{org_id}/audit-log",
    response_model=Page[AuditEntryOut],
    dependencies=[RequireAdmin],
)
async def list_audit_log(
    org_id: Annotated[uuid.UUID, Path()],
    session: SessionDep,
    page: PageQuery,
) -> Page[AuditEntryOut]:
    entries = await orgs_service.list_audit(
        session, org_id=org_id, limit=page.limit, cursor=page.cursor
    )
    return Page(
        items=[AuditEntryOut.model_validate(e) for e in entries.items],
        next_cursor=entries.next_cursor,
    )
