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
from app.db.pagination import PageResult, keyset_page
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


async def list_for_user(
    session: AsyncSession, user_id: uuid.UUID, *, limit: int = 50, cursor: str | None = None
) -> PageResult[Organization]:
    return await keyset_page(
        session,
        select(Organization)
        .join(Membership, Membership.org_id == Organization.id)
        .where(Membership.user_id == user_id),
        [Organization.created_at, Organization.id],
        limit=limit,
        cursor=cursor,
        descending=False,
    )


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


async def list_members(
    session: AsyncSession, org_id: uuid.UUID, *, limit: int = 50, cursor: str | None = None
) -> PageResult[tuple[User, Membership]]:
    return await keyset_page(
        session,
        select(User, Membership)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.org_id == org_id),
        [Membership.created_at, Membership.id],
        limit=limit,
        cursor=cursor,
        descending=False,
        entities=2,
    )


def _authorise_role_change(
    actor: MemberRole, current: MemberRole, new: MemberRole, *, is_self: bool
) -> None:
    """Who may move whom, where.

    The route only asked "is the actor at least an admin?", which let an admin
    promote *themselves* to owner and then demote the founding owner — a full
    takeover in two 200s. That needs both sides of the change, so it lives
    here rather than in a route dependency:

    - anyone may lower (or keep) their own role;
    - owners may make any change (the last-owner rule still applies after);
    - otherwise you cannot grant above your own rank, and cannot change
      someone ranked at or above you — so one admin cannot strip the rest.
    """
    if is_self and new.rank <= current.rank:
        return
    if actor is MemberRole.owner:
        return
    if new.rank > actor.rank:
        raise Forbidden("You cannot grant a role above your own", code="role_change_not_allowed")
    if current.rank >= actor.rank:
        raise Forbidden(
            "You cannot change the role of someone at or above your own rank",
            code="role_change_not_allowed",
        )


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
    actor = await session.scalar(
        select(Membership).where(Membership.org_id == org_id, Membership.user_id == actor_user_id)
    )
    if actor is None:
        raise Forbidden("You are not a member of this org")
    _authorise_role_change(
        actor.role, membership.role, new_role, is_self=actor_user_id == target_user_id
    )

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
    # The same rule as a role change (task 6.3): nobody hands out a role
    # above their own. Without it an admin invited an address of their own
    # as owner, accepted it, and demoted the founder.
    actor = await session.scalar(
        select(Membership).where(Membership.org_id == org_id, Membership.user_id == actor_user_id)
    )
    if actor is None or role.rank > actor.role.rank:
        raise Forbidden(
            "You cannot invite someone to a role above your own", code="invite_role_not_allowed"
        )
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
    # Flushed first so the audit entry names the invite itself; it used to
    # record the placeholder "(pending)", which matched nothing.
    await session.flush()
    await audit.record(
        session,
        action="org.invite.create",
        org_id=org_id,
        actor_user_id=actor_user_id,
        target_type="invite",
        target_id=invite.id,
        meta={"email": email, "role": role.value},
        ip=ip,
    )
    # Committed before the link is handed out: an invitee clicking it at once
    # must not race the request teardown's commit.
    await session.commit()
    return invite, raw


async def preview_invite(session: AsyncSession, *, raw_token: str) -> tuple[Invite, Organization]:
    invite = await session.scalar(select(Invite).where(Invite.token_hash == _hash_token(raw_token)))
    org = await session.get(Organization, invite.org_id) if invite is not None else None
    if invite is None or org is None:
        raise NotFound("Invite not found")
    return invite, org


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
        await session.commit()
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
    # Explicit, like every other mutating service: the UI switches to the new
    # org and lists it immediately, and the request's own commit only runs
    # after the response has been sent.
    await session.commit()
    return membership


async def list_audit(
    session: AsyncSession, *, org_id: uuid.UUID, limit: int = 50, cursor: str | None = None
) -> PageResult[AuditLog]:
    # This was the one list with pagination, by OFFSET, which on an
    # append-only log shifts under the reader with every new entry.
    return await keyset_page(
        session,
        select(AuditLog).where(AuditLog.org_id == org_id),
        [AuditLog.created_at, AuditLog.id],
        limit=limit,
        cursor=cursor,
    )


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
    "preview_invite",
]
