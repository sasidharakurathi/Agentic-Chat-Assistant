"""Groups (Phase 8.5): admins make them and choose who is in them; anyone
building an assistant sees their names, to choose an audience. Each change
is in the audit log."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import ClientIP, OrgMembership, SessionDep, require_role
from app.api.errors import BadRequest, Conflict, NotFound
from app.models.enums import MemberRole
from app.models.group import Group, GroupMember
from app.models.membership import Membership
from app.models.rag import DataSource
from app.models.user import User
from app.schemas.common import Message
from app.schemas.listing import GroupIn, GroupMemberIn, GroupMemberOut, GroupOut, GroupUpdate
from app.services import audit, knowledge_access, listing

router = APIRouter(tags=["groups"])

RequireAdmin = Depends(require_role(MemberRole.admin))
OrgId = Annotated[uuid.UUID, Path()]


async def _group(session: SessionDep, org_id: uuid.UUID, group_id: uuid.UUID) -> Group:
    group = await session.get(Group, group_id)
    if group is None or group.org_id != org_id:
        raise NotFound("Group not found")
    return group


async def _out(session: SessionDep, group: Group) -> GroupOut:
    count = await session.scalar(
        select(func.count()).select_from(GroupMember).where(GroupMember.group_id == group.id)
    )
    return GroupOut(
        id=group.id,
        name=group.name,
        description=group.description,
        members=int(count or 0),
        created_at=group.created_at,
    )


@router.get("/orgs/{org_id}/groups", response_model=list[GroupOut])
async def list_groups(
    org_id: OrgId, membership: OrgMembership, session: SessionDep
) -> list[GroupOut]:
    """Every group, by name: for choosing an audience as well as managing."""
    groups = (
        await session.scalars(select(Group).where(Group.org_id == org_id).order_by(Group.name))
    ).all()
    return [await _out(session, g) for g in groups]


@router.post(
    "/orgs/{org_id}/groups",
    response_model=GroupOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_group(
    org_id: OrgId,
    body: GroupIn,
    actor: Annotated[Membership, RequireAdmin],
    session: SessionDep,
    ip: ClientIP,
) -> GroupOut:
    group = Group(
        org_id=org_id,
        name=body.name.strip(),
        description=body.description.strip(),
        created_by=actor.user_id,
    )
    session.add(group)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict("A group has that name already", code="group_exists") from exc
    await audit.record(
        session,
        action="group.create",
        org_id=org_id,
        actor_user_id=actor.user_id,
        target_type="group",
        target_id=group.id,
        meta={"name": group.name},
        ip=ip,
    )
    await session.commit()
    return await _out(session, group)


@router.patch("/orgs/{org_id}/groups/{group_id}", response_model=GroupOut)
async def update_group(
    org_id: OrgId,
    group_id: Annotated[uuid.UUID, Path()],
    body: GroupUpdate,
    actor: Annotated[Membership, RequireAdmin],
    session: SessionDep,
    ip: ClientIP,
) -> GroupOut:
    group = await _group(session, org_id, group_id)
    before = {"name": group.name, "description": group.description}
    if body.name is not None:
        group.name = body.name.strip()
    if body.description is not None:
        group.description = body.description.strip()
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict("A group has that name already", code="group_exists") from exc
    await audit.record(
        session,
        action="group.update",
        org_id=org_id,
        actor_user_id=actor.user_id,
        target_type="group",
        target_id=group.id,
        meta={"from": before, "to": {"name": group.name, "description": group.description}},
        ip=ip,
    )
    await session.commit()
    return await _out(session, group)


@router.delete("/orgs/{org_id}/groups/{group_id}", response_model=Message)
async def delete_group(
    org_id: OrgId,
    group_id: Annotated[uuid.UUID, Path()],
    actor: Annotated[Membership, RequireAdmin],
    session: SessionDep,
    ip: ClientIP,
) -> Message:
    """Refused while a live or waiting version names it: its people would
    lose the assistant without anyone deciding so."""
    group = await _group(session, org_id, group_id)
    used = await listing.groups_in_use(session, group.id)
    if used:
        raise Conflict(
            f"{', '.join(used[:5])} {'is' if len(used) == 1 else 'are'} for this group: "
            "change their audience first",
            code="group_in_use",
        )
    name = group.name
    # Knowledge that named it (Phase 9.4) now reaches only its other groups,
    # or nobody, until someone gives it readers again: never everyone.
    named = [
        n
        for n, ids in (
            await session.execute(
                select(DataSource.name, DataSource.group_ids).where(DataSource.org_id == org_id)
            )
        ).all()
        if str(group_id) in {str(i) for i in ids or []}
    ]
    await session.delete(group)
    await knowledge_access.bump_epoch(session, org_id)
    await audit.record(
        session,
        action="group.delete",
        org_id=org_id,
        actor_user_id=actor.user_id,
        target_type="group",
        target_id=group_id,
        meta={"name": name, "sources": named[:20]},
        ip=ip,
    )
    await session.commit()
    if named:
        return Message(
            message=f"Group deleted. {len(named)} knowledge source(s) named it and now reach "
            "only their other groups: give them readers again in their assistants' Sources."
        )
    return Message(message="Group deleted.")


@router.get(
    "/orgs/{org_id}/groups/{group_id}/members",
    response_model=list[GroupMemberOut],
    dependencies=[RequireAdmin],
)
async def list_group_members(
    org_id: OrgId, group_id: Annotated[uuid.UUID, Path()], session: SessionDep
) -> list[GroupMemberOut]:
    await _group(session, org_id, group_id)
    rows = (
        await session.execute(
            select(User.id, User.email, User.name)
            .join(GroupMember, GroupMember.user_id == User.id)
            .where(GroupMember.group_id == group_id)
            .order_by(User.email)
        )
    ).all()
    return [GroupMemberOut(user_id=i, email=e, name=n) for i, e, n in rows]


@router.post(
    "/orgs/{org_id}/groups/{group_id}/members",
    response_model=list[GroupMemberOut],
    status_code=status.HTTP_201_CREATED,
)
async def add_group_member(
    org_id: OrgId,
    group_id: Annotated[uuid.UUID, Path()],
    body: GroupMemberIn,
    actor: Annotated[Membership, RequireAdmin],
    session: SessionDep,
    ip: ClientIP,
) -> list[GroupMemberOut]:
    group = await _group(session, org_id, group_id)
    member = await session.scalar(
        select(Membership).where(
            Membership.org_id == org_id,
            Membership.user_id == body.user_id,
            Membership.deactivated_at.is_(None),
        )
    )
    if member is None:
        raise BadRequest("That person isn't an active member here", code="not_a_member")
    exists = await session.scalar(
        select(GroupMember.id).where(
            GroupMember.group_id == group.id, GroupMember.user_id == body.user_id
        )
    )
    if exists is None:
        session.add(
            GroupMember(
                org_id=org_id, group_id=group.id, user_id=body.user_id, added_by=actor.user_id
            )
        )
        await audit.record(
            session,
            action="group.member.add",
            org_id=org_id,
            actor_user_id=actor.user_id,
            target_type="group",
            target_id=group.id,
            meta={"user_id": str(body.user_id)},
            ip=ip,
        )
        await session.commit()
    return await list_group_members(org_id, group_id, session)


@router.delete(
    "/orgs/{org_id}/groups/{group_id}/members/{user_id}", response_model=list[GroupMemberOut]
)
async def remove_group_member(
    org_id: OrgId,
    group_id: Annotated[uuid.UUID, Path()],
    user_id: Annotated[uuid.UUID, Path()],
    actor: Annotated[Membership, RequireAdmin],
    session: SessionDep,
    ip: ClientIP,
) -> list[GroupMemberOut]:
    group = await _group(session, org_id, group_id)
    row = await session.scalar(
        select(GroupMember).where(GroupMember.group_id == group.id, GroupMember.user_id == user_id)
    )
    if row is not None:
        await session.delete(row)
        # They may read less now (Phase 9.4).
        await knowledge_access.bump_epoch(session, org_id)
        await audit.record(
            session,
            action="group.member.remove",
            org_id=org_id,
            actor_user_id=actor.user_id,
            target_type="group",
            target_id=group.id,
            meta={"user_id": str(user_id)},
            ip=ip,
        )
        await session.commit()
    return await list_group_members(org_id, group_id, session)


__all__ = ["router"]
