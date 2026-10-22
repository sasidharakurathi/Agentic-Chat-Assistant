"""When someone leaves, and comes back (Phase 7b.4).

Deactivation is how a person is removed from an organisation. The
membership row stays, deactivated, rather than being deleted: the audit log,
the approvals they decided and the assistants they built keep their name.
In one transaction, deactivating:

- ends their access to the org from their next request (`member_of`
  refuses a deactivated membership), and signs them out everywhere: every
  refresh token they hold is revoked, so no phone or tab renews a session.
  With no other organisation left, the account is switched off too;
- hands the assistants they created to someone who stays (by default the
  admin doing it), each transfer audited, so every assistant has an owner
  who still works there;
- withdraws their co-editor grants, so reactivating later doesn't quietly
  restore them;
- closes the approvals their conversations are waiting on: nothing a
  leaver asked for runs after they've gone;
- revokes the invites they sent and that nobody has used yet.

After the commit, any answer being written in their conversations stops.

`leaving` lists what they leave behind before an admin confirms: the
assistants (with the database connections and MCP servers on them, whose
credentials the leaver may know and should be rotated), pending approvals,
open invites and co-editor grants.

Reactivating brings back their membership and their account, nothing else:
ownership and grants stay where deactivation put them.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import interrupts
from app.api.errors import BadRequest, Conflict, Forbidden, NotFound
from app.models.approval import Approval, ApprovalStatus
from app.models.assistant import Assistant, AssistantEditor
from app.models.conversation import Conversation
from app.models.enums import MemberRole
from app.models.integration import DbConnection, McpServer
from app.models.invite import Invite
from app.models.membership import Membership
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.services import audit, turns


async def _membership(session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID) -> Membership:
    m = await session.scalar(
        select(Membership).where(Membership.org_id == org_id, Membership.user_id == user_id)
    )
    if m is None:
        raise NotFound("That user is not a member of this org")
    return m


async def ensure_not_last_active_owner(
    session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    """An organisation keeps at least one active owner: someone who can
    still sign in and whose membership is current. Counting owners whose
    account was switched off would let the last real one go."""
    others = await session.scalar(
        select(func.count())
        .select_from(Membership)
        .join(User, User.id == Membership.user_id)
        .where(
            Membership.org_id == org_id,
            Membership.role == MemberRole.owner,
            Membership.deactivated_at.is_(None),
            User.is_active.is_(True),
            Membership.user_id != user_id,
        )
    )
    if not others:
        raise BadRequest(
            "An organization must keep at least one active owner. Make someone else an owner first",
            code="last_owner",
        )


def authorise(actor: Membership, target: Membership) -> None:
    """The rank rule of role changes (`orgs._authorise_role_change`):
    nobody removes someone at or above their own rank, except an owner; and
    nobody removes themselves this way."""
    if actor.user_id == target.user_id:
        raise Forbidden("You can't deactivate yourself", code="cannot_deactivate_self")
    if actor.role is MemberRole.owner:
        return
    if target.role.rank >= actor.role.rank:
        raise Forbidden(
            "You can't remove someone at or above your own rank", code="role_change_not_allowed"
        )


# ── what a leaver leaves behind ──────────────────────────────


@dataclass
class Leaving:
    assistants: list[dict[str, str]] = field(default_factory=list)
    connections: list[dict[str, str]] = field(default_factory=list)
    mcp_servers: list[dict[str, str]] = field(default_factory=list)
    pending_approvals: int = 0
    open_invites: int = 0
    editor_of: list[dict[str, str]] = field(default_factory=list)


async def leaving(session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID) -> Leaving:
    """What deactivating this person would hand on, close or revoke."""
    await _membership(session, org_id, user_id)
    built = (
        await session.scalars(
            select(Assistant).where(Assistant.org_id == org_id, Assistant.created_by == user_id)
        )
    ).all()
    edited = (
        await session.execute(
            select(Assistant.id, Assistant.name)
            .join(AssistantEditor, AssistantEditor.assistant_id == Assistant.id)
            .where(AssistantEditor.org_id == org_id, AssistantEditor.user_id == user_id)
        )
    ).all()
    # Credentials they could have seen: on the assistants they built or edited.
    reach = [a.id for a in built] + [aid for aid, _ in edited]
    names: dict[uuid.UUID, str] = {a.id: a.name for a in built}
    names.update((aid, name) for aid, name in edited)
    connections = (
        await session.execute(
            select(DbConnection.name, DbConnection.assistant_id).where(
                DbConnection.assistant_id.in_(reach)
            )
        )
    ).all()
    servers = (
        await session.execute(
            select(McpServer.name, McpServer.assistant_id).where(McpServer.assistant_id.in_(reach))
        )
    ).all()
    pending = await session.scalar(
        select(func.count())
        .select_from(Approval)
        .where(
            Approval.org_id == org_id,
            Approval.requested_by == user_id,
            Approval.status == ApprovalStatus.pending,
        )
    )
    invites = await session.scalar(
        select(func.count())
        .select_from(Invite)
        .where(
            Invite.org_id == org_id,
            Invite.invited_by == user_id,
            Invite.accepted_at.is_(None),
            Invite.revoked_at.is_(None),
        )
    )
    return Leaving(
        assistants=[{"id": str(a.id), "name": a.name} for a in built],
        connections=[{"name": n, "assistant": names[a]} for n, a in connections],
        mcp_servers=[{"name": n, "assistant": names[a]} for n, a in servers],
        pending_approvals=int(pending or 0),
        open_invites=int(invites or 0),
        editor_of=[{"id": str(i), "name": n} for i, n in edited],
    )


# ── deactivate / reactivate ──────────────────────────────────


async def deactivate(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    target_user_id: uuid.UUID,
    transfer_to: uuid.UUID | None = None,
    ip: str | None = None,
) -> Membership:
    actor = await _membership(session, org_id, actor_user_id)
    target = await _membership(session, org_id, target_user_id)
    if not target.active:
        raise Conflict("They are already deactivated", code="member_deactivated")
    authorise(actor, target)
    if target.role is MemberRole.owner:
        await ensure_not_last_active_owner(session, org_id, target_user_id)
    heir_id = transfer_to or actor_user_id
    heir = await _membership(session, org_id, heir_id)
    heir_user = await session.get(User, heir_id)
    if (
        heir_id == target_user_id
        or not heir.active
        or heir_user is None
        or not heir_user.is_active
        # An employee builds nothing, so can own nothing (Phase 8.1).
        or not heir.role.satisfies(MemberRole.member)
    ):
        raise BadRequest(
            "Hand their assistants to someone who stays in the organization and builds",
            code="invalid_transfer",
        )
    now = datetime.now(UTC)

    target.deactivated_at, target.deactivated_by = now, actor_user_id
    user = await session.get(User, target_user_id)
    assert user is not None
    # Across orgs on purpose (`all_orgs`): the request is bound to this one.
    still_elsewhere = await session.scalar(
        select(func.count())
        .select_from(Membership)
        .where(
            Membership.user_id == target_user_id,
            Membership.org_id != org_id,
            Membership.deactivated_at.is_(None),
        )
        .execution_options(all_orgs=True)
    )
    if not still_elsewhere:
        user.is_active = False
    sessions = await session.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == target_user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
        .execution_options(synchronize_session=False)
    )

    handed = (
        await session.scalars(
            select(Assistant).where(
                Assistant.org_id == org_id, Assistant.created_by == target_user_id
            )
        )
    ).all()
    for assistant in handed:
        assistant.created_by = heir_id
        await audit.record(
            session,
            action="assistant.transfer",
            org_id=org_id,
            actor_user_id=actor_user_id,
            target_type="assistant",
            target_id=assistant.id,
            meta={"from": str(target_user_id), "to": str(heir_id), "reason": "deactivated"},
            ip=ip,
        )
    grants = await session.execute(
        delete(AssistantEditor)
        .where(AssistantEditor.org_id == org_id, AssistantEditor.user_id == target_user_id)
        .execution_options(synchronize_session=False)
    )
    closed = await session.execute(
        update(Approval)
        .where(
            Approval.org_id == org_id,
            Approval.requested_by == target_user_id,
            Approval.status == ApprovalStatus.pending,
        )
        .values(
            status=ApprovalStatus.cancelled,
            decided_at=now,
            sealed_ciphertext=None,
            sealed_dek=None,
            sealed_nonce=None,
        )
        .execution_options(synchronize_session=False)
    )
    revoked = await session.execute(
        update(Invite)
        .where(
            Invite.org_id == org_id,
            Invite.invited_by == target_user_id,
            Invite.accepted_at.is_(None),
            Invite.revoked_at.is_(None),
        )
        .values(revoked_at=now, revoked_by=actor_user_id)
        .execution_options(synchronize_session=False)
    )
    await audit.record(
        session,
        action="org.member.deactivate",
        org_id=org_id,
        actor_user_id=actor_user_id,
        target_type="user",
        target_id=target_user_id,
        meta={
            "role": target.role.value,
            "email": user.email,
            "account_switched_off": not still_elsewhere,
            "sessions_revoked": sessions.rowcount,  # type: ignore[attr-defined]
            "assistants_to": str(heir_id),
            "assistants": [str(a.id) for a in handed],
            "editor_grants_removed": grants.rowcount,  # type: ignore[attr-defined]
            "approvals_closed": closed.rowcount,  # type: ignore[attr-defined]
            "invites_revoked": revoked.rowcount,  # type: ignore[attr-defined]
        },
        ip=ip,
    )
    await session.commit()

    # After the commit: an answer being written in their conversations stops.
    theirs = list(
        (
            await session.scalars(
                select(Conversation.id).where(
                    Conversation.org_id == org_id, Conversation.created_by == target_user_id
                )
            )
        ).all()
    )
    for cid in await turns.running_among(theirs):
        interrupts.interrupt_local(cid)
        await interrupts.publish(cid)
    await session.refresh(target)
    return target


async def reactivate(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    target_user_id: uuid.UUID,
    ip: str | None = None,
) -> Membership:
    """Their membership and their account come back; their old assistants
    and co-editor grants don't: those stay where deactivation put them."""
    actor = await _membership(session, org_id, actor_user_id)
    target = await _membership(session, org_id, target_user_id)
    if target.active:
        raise Conflict("They are already active", code="member_active")
    authorise(actor, target)
    target.deactivated_at, target.deactivated_by = None, None
    user = await session.get(User, target_user_id)
    assert user is not None
    user.is_active = True
    await audit.record(
        session,
        action="org.member.reactivate",
        org_id=org_id,
        actor_user_id=actor_user_id,
        target_type="user",
        target_id=target_user_id,
        meta={"role": target.role.value, "email": user.email},
        ip=ip,
    )
    await session.commit()
    await session.refresh(target)
    return target


__all__ = [
    "Leaving",
    "authorise",
    "deactivate",
    "ensure_not_last_active_owner",
    "leaving",
    "reactivate",
]
