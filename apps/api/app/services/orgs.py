"""Organization + membership + invite operations."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import BadRequest, Conflict, Forbidden, NotFound
from app.config import settings
from app.models.audit_log import AuditLog
from app.models.enums import MemberRole
from app.models.invite import Invite
from app.models.membership import Membership
from app.models.organization import Organization
from app.models.user import User
from app.services import audit
from app.services.slug import slugify

INVITE_TTL = timedelta(days=7)


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


async def list_for_user(session: AsyncSession, user_id: uuid.UUID) -> list[Organization]:
    rows = await session.scalars(
        select(Organization)
        .join(Membership, Membership.org_id == Organization.id)
        .where(Membership.user_id == user_id)
        .order_by(Organization.created_at)
    )
    return list(rows)


async def create(
    session: AsyncSession, *, user_id: uuid.UUID, name: str, ip: str | None = None
) -> Organization:
    slug = slugify(name)[:100]
    if await session.scalar(select(Organization.id).where(Organization.slug == slug)) is not None:
        slug = f"{slug}-{secrets.token_hex(3)}"
    org = Organization(name=name.strip(), slug=slug, is_personal=False)
    session.add(org)
    await session.flush()
    session.add(Membership(org_id=org.id, user_id=user_id, role=MemberRole.owner))
    await audit.record(
        session,
        action="org.create",
        org_id=org.id,
        actor_user_id=user_id,
        target_type="org",
        target_id=org.id,
        ip=ip,
    )
    await session.flush()
    return org


async def list_members(session: AsyncSession, org_id: uuid.UUID) -> list[tuple[User, Membership]]:
    rows = await session.execute(
        select(User, Membership)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.org_id == org_id)
        .order_by(Membership.created_at)
    )
    return [(u, m) for u, m in rows.all()]


async def change_role(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    target_user_id: uuid.UUID,
    new_role: MemberRole,
    ip: str | None = None,
) -> Membership:
    membership = await session.scalar(
        select(Membership).where(Membership.org_id == org_id, Membership.user_id == target_user_id)
    )
    if membership is None:
        raise NotFound("That user is not a member of this org")

    # Don't allow removing the last owner.
    if membership.role is MemberRole.owner and new_role is not MemberRole.owner:
        owners = await session.scalars(
            select(Membership).where(
                Membership.org_id == org_id, Membership.role == MemberRole.owner
            )
        )
        if len({o.user_id for o in owners}) <= 1:
            raise BadRequest("An organization must keep at least one owner")

    old_role = membership.role
    membership.role = new_role
    await audit.record(
        session,
        action="org.member.role_change",
        org_id=org_id,
        actor_user_id=actor_user_id,
        target_type="user",
        target_id=target_user_id,
        meta={"from": old_role.value, "to": new_role.value},
        ip=ip,
    )
    await session.flush()
    return membership


async def create_invite(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    email: str,
    role: MemberRole,
    ip: str | None = None,
) -> tuple[Invite, str]:
    email = email.strip().lower()
    already = await session.scalar(
        select(Membership.id)
        .join(User, User.id == Membership.user_id)
        .where(Membership.org_id == org_id, User.email == email)
    )
    if already is not None:
        raise Conflict("That email is already a member")

    raw = secrets.token_urlsafe(32)
    invite = Invite(
        org_id=org_id,
        email=email,
        role=role,
        token_hash=_hash_token(raw),
        invited_by=actor_user_id,
        expires_at=datetime.now(UTC) + INVITE_TTL,
    )
    session.add(invite)
    await audit.record(
        session,
        action="org.invite.create",
        org_id=org_id,
        actor_user_id=actor_user_id,
        target_type="invite",
        target_id="(pending)",
        meta={"email": email, "role": role.value},
        ip=ip,
    )
    await session.flush()
    return invite, raw


async def accept_invite(
    session: AsyncSession, *, raw_token: str, user: User, ip: str | None = None
) -> Membership:
    invite = await session.scalar(select(Invite).where(Invite.token_hash == _hash_token(raw_token)))
    if invite is None or invite.accepted_at is not None:
        raise NotFound("Invite not found or already used")
    if invite.expires_at <= datetime.now(UTC):
        raise BadRequest("Invite has expired")
    if invite.email != user.email.lower():
        raise Forbidden("This invite was issued for a different email")

    existing = await session.scalar(
        select(Membership).where(Membership.org_id == invite.org_id, Membership.user_id == user.id)
    )
    if existing is not None:
        invite.accepted_at = datetime.now(UTC)
        await session.flush()
        return existing

    membership = Membership(org_id=invite.org_id, user_id=user.id, role=invite.role)
    session.add(membership)
    invite.accepted_at = datetime.now(UTC)
    await audit.record(
        session,
        action="org.invite.accept",
        org_id=invite.org_id,
        actor_user_id=user.id,
        target_type="user",
        target_id=user.id,
        ip=ip,
    )
    await session.flush()
    return membership


async def list_audit(
    session: AsyncSession, *, org_id: uuid.UUID, limit: int = 100, offset: int = 0
) -> list[AuditLog]:
    rows = await session.scalars(
        select(AuditLog)
        .where(AuditLog.org_id == org_id)
        .order_by(AuditLog.created_at.desc())
        .limit(min(limit, 500))
        .offset(offset)
    )
    return list(rows)


def invite_accept_url(raw_token: str) -> str:
    return f"{settings.app_base_url.rstrip('/')}/invites/{raw_token}"


__all__ = [
    "accept_invite",
    "change_role",
    "create",
    "create_invite",
    "invite_accept_url",
    "list_audit",
    "list_for_user",
    "list_members",
]
