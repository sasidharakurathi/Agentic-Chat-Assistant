"""Organization + membership + invite operations."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import Select, select, update
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


def invite_ttl() -> timedelta:
    return timedelta(days=settings.invite_ttl_days)


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


async def any_open(session: AsyncSession) -> bool:
    """Whether any organisation is open (not archived)."""
    found = await session.scalar(
        select(Organization.id).where(Organization.archived_at.is_(None)).limit(1)
    )
    return found is not None


async def list_for_user(
    session: AsyncSession, user_id: uuid.UUID, *, limit: int = 50, cursor: str | None = None
) -> PageResult[Organization]:
    return await keyset_page(
        session,
        select(Organization)
        .join(Membership, Membership.org_id == Organization.id)
        .where(
            Membership.user_id == user_id,
            Membership.deactivated_at.is_(None),
            Organization.archived_at.is_(None),
        ),
        [Organization.created_at, Organization.id],
        limit=limit,
        cursor=cursor,
        descending=False,
    )


async def create(
    session: AsyncSession, *, user_id: uuid.UUID, name: str, ip: str | None = None
) -> Organization:
    if settings.org_mode == "single" and await any_open(session):
        # One company organisation (Phase 7b.8): another would sit outside
        # its admins, its budgets and its audit log.
        raise Forbidden(
            "This server has one organisation; nobody can create another",
            code="org_creation_closed",
        )
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
    if not membership.active:
        raise Conflict(
            "They are deactivated: reactivate them before changing their role",
            code="member_deactivated",
        )
    actor = await session.scalar(
        select(Membership).where(
            Membership.org_id == org_id,
            Membership.user_id == actor_user_id,
            Membership.deactivated_at.is_(None),
        )
    )
    if actor is None:
        raise Forbidden("You are not a member of this org")
    _authorise_role_change(
        actor.role, membership.role, new_role, is_self=actor_user_id == target_user_id
    )

    # Don't allow removing the last active owner (Phase 7b.4: an owner who
    # can't sign in, or was removed, doesn't count).
    if membership.role is MemberRole.owner and new_role is not MemberRole.owner:
        from app.services.people import ensure_not_last_active_owner

        await ensure_not_last_active_owner(session, org_id, target_user_id)

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
        select(Membership).where(
            Membership.org_id == org_id,
            Membership.user_id == actor_user_id,
            Membership.deactivated_at.is_(None),
        )
    )
    if actor is None or role.rank > actor.role.rank:
        raise Forbidden(
            "You cannot invite someone to a role above your own", code="invite_role_not_allowed"
        )
    already = await session.scalar(
        select(Membership)
        .join(User, User.id == Membership.user_id)
        .where(Membership.org_id == org_id, User.email == email)
    )
    if already is not None and not already.active:
        raise Conflict(
            "That person was deactivated. Reactivate them from Members instead of inviting them",
            code="member_deactivated",
        )
    if already is not None:
        raise Conflict("That email is already a member")

    # One open invite per address (Phase 7b.4): a new link voids the old.
    now = datetime.now(UTC)
    superseded = await session.execute(
        update(Invite)
        .where(
            Invite.org_id == org_id,
            Invite.email == email,
            Invite.accepted_at.is_(None),
            Invite.revoked_at.is_(None),
        )
        .values(revoked_at=now, revoked_by=actor_user_id)
        .execution_options(synchronize_session=False)
    )

    raw = secrets.token_urlsafe(32)
    invite = Invite(
        org_id=org_id,
        email=email,
        role=role,
        token_hash=_hash_token(raw),
        invited_by=actor_user_id,
        expires_at=now + invite_ttl(),
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
        meta={
            "email": email,
            "role": role.value,
            "replaced": superseded.rowcount,  # type: ignore[attr-defined]
        },
        ip=ip,
    )
    # Committed before the link is handed out: an invitee clicking it at once
    # must not race the request teardown's commit.
    await session.commit()
    return invite, raw


async def preview_invite(session: AsyncSession, *, raw_token: str) -> tuple[Invite, Organization]:
    invite = await session.scalar(select(Invite).where(Invite.token_hash == _hash_token(raw_token)))
    org = await session.get(Organization, invite.org_id) if invite is not None else None
    # An archived org's invites lead nowhere (Phase 7b.8).
    if invite is None or org is None or org.archived_at is not None:
        raise NotFound("Invite not found")
    return invite, org


async def inviter_still_may(session: AsyncSession, invite: Invite) -> bool:
    """Whether the person who sent an invite could still send it (Phase
    7b.4): an active admin or owner of the org whose rank covers the role
    it grants. An invite from someone since demoted or removed opens
    nothing; an admin who knew of it sends a new one."""
    if invite.invited_by is None:
        return False
    row = await session.execute(
        select(Membership.role, User.is_active)
        .join(User, User.id == Membership.user_id)
        .where(
            Membership.org_id == invite.org_id,
            Membership.user_id == invite.invited_by,
            Membership.deactivated_at.is_(None),
        )
        .execution_options(all_orgs=True)
    )
    found = row.first()
    if found is None:
        return False
    role, active = found
    return bool(active) and role.satisfies(MemberRole.admin) and invite.role.rank <= role.rank


async def check_open(session: AsyncSession, invite: Invite | None) -> Invite:
    """An invite that can still be used: not used, not revoked, not
    expired, and still backed by its sender."""
    if invite is None or invite.accepted_at is not None or invite.revoked_at is not None:
        raise NotFound("Invite not found, already used or withdrawn")
    org = await session.get(Organization, invite.org_id)
    if org is None or org.archived_at is not None:
        raise NotFound("Invite not found, already used or withdrawn")
    if invite.expires_at <= datetime.now(UTC):
        raise BadRequest("Invite has expired", code="invite_expired")
    if not await inviter_still_may(session, invite):
        raise Forbidden(
            "This invite is no longer valid. Ask an admin for a new one",
            code="invite_no_longer_valid",
        )
    return invite


async def accept_invite(
    session: AsyncSession, *, raw_token: str, user: User, ip: str | None = None
) -> Membership:
    invite = await check_open(
        session,
        await session.scalar(select(Invite).where(Invite.token_hash == _hash_token(raw_token))),
    )
    if invite.email != user.email.lower():
        raise Forbidden("This invite was issued for a different email")
    return await join(session, invite, user, ip=ip)


async def join(
    session: AsyncSession, invite: Invite, user: User, *, ip: str | None = None
) -> Membership:
    """Use an invite: `user` joins its org with its role. Committed."""
    existing = await session.scalar(
        select(Membership)
        .where(Membership.org_id == invite.org_id, Membership.user_id == user.id)
        .execution_options(all_orgs=True)
    )
    if existing is not None and not existing.active:
        raise Conflict(
            "Your membership was deactivated. Ask an admin to reactivate it",
            code="member_deactivated",
        )
    if existing is not None:
        invite.accepted_at = datetime.now(UTC)
        await session.commit()
        return existing

    membership = Membership(org_id=invite.org_id, user_id=user.id, role=invite.role)
    session.add(membership)
    invite.accepted_at = datetime.now(UTC)
    # The invite was for their address (Phase 7b.6): as good as a link opened.
    user.email_verified_at = user.email_verified_at or invite.accepted_at
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


async def list_invites(session: AsyncSession, org_id: uuid.UUID) -> list[Invite]:
    """Invites nobody has used or withdrawn yet, expired ones included (so
    an admin sees a link went stale), newest first."""
    rows = await session.scalars(
        select(Invite)
        .where(
            Invite.org_id == org_id,
            Invite.accepted_at.is_(None),
            Invite.revoked_at.is_(None),
        )
        .order_by(Invite.created_at.desc())
    )
    return list(rows)


async def revoke_invite(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    invite_id: uuid.UUID,
    actor: Membership,
    ip: str | None = None,
) -> Invite:
    """Withdraw an unused invite: its link opens nothing afterwards. Not one
    granting a role above the actor's own."""
    invite = await session.scalar(
        select(Invite).where(Invite.id == invite_id, Invite.org_id == org_id)
    )
    if invite is None or invite.accepted_at is not None or invite.revoked_at is not None:
        raise NotFound("Invite not found, already used or withdrawn")
    if invite.role.rank > actor.role.rank:
        raise Forbidden(
            "You can't withdraw an invite to a role above your own",
            code="invite_role_not_allowed",
        )
    invite.revoked_at, invite.revoked_by = datetime.now(UTC), actor.user_id
    await audit.record(
        session,
        action="org.invite.revoke",
        org_id=org_id,
        actor_user_id=actor.user_id,
        target_type="invite",
        target_id=invite.id,
        meta={"email": invite.email, "role": invite.role.value},
        ip=ip,
    )
    await session.commit()
    return invite


@dataclass(frozen=True)
class AuditFilter:
    """What the audit page narrows to (Phase 7b.7). `action` is a prefix:
    `assistant.` is everything about assistants, an exact name one kind."""

    action: str | None = None
    actor_id: uuid.UUID | None = None
    target_id: str | None = None
    since: datetime | None = None
    until: datetime | None = None


def audit_query(org_id: uuid.UUID, f: AuditFilter) -> Select[tuple[AuditLog]]:
    stmt = select(AuditLog).where(AuditLog.org_id == org_id)
    if f.action:
        stmt = stmt.where(AuditLog.action.startswith(f.action))
    if f.actor_id is not None:
        stmt = stmt.where(AuditLog.actor_user_id == f.actor_id)
    if f.target_id:
        stmt = stmt.where(AuditLog.target_id == f.target_id)
    if f.since is not None:
        stmt = stmt.where(AuditLog.created_at >= f.since)
    if f.until is not None:
        stmt = stmt.where(AuditLog.created_at <= f.until)
    return stmt


async def list_audit(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    limit: int = 50,
    cursor: str | None = None,
    filters: AuditFilter | None = None,
) -> PageResult[AuditLog]:
    # This was the one list with pagination, by OFFSET, which on an
    # append-only log shifts under the reader with every new entry.
    return await keyset_page(
        session,
        audit_query(org_id, filters or AuditFilter()),
        [AuditLog.created_at, AuditLog.id],
        limit=limit,
        cursor=cursor,
    )


def invite_accept_url(raw_token: str) -> str:
    return f"{settings.app_base_url.rstrip('/')}/invites/{raw_token}"


__all__ = [
    "AuditFilter",
    "accept_invite",
    "any_open",
    "audit_query",
    "change_role",
    "check_open",
    "create",
    "create_invite",
    "invite_accept_url",
    "inviter_still_may",
    "join",
    "list_audit",
    "list_for_user",
    "list_invites",
    "list_members",
    "preview_invite",
    "revoke_invite",
]
