"""Who reaches Assistant Studio at all (Phase 7a.3).

Every route that acts inside an org finds the caller's membership through
`member_of`, which also applies the floor: the lowest role that may use the
Studio. A role added below it (the Employee role in Phase 8, for people who
only chat) is then refused on every one of those routes at once, instead of
being let in wherever a route forgot to check. Routes meant for such a role
will get their own dependencies; nothing here grants access by omission.

Routes that need no org at all (sign-in, the caller's own profile, the
public schemas) are listed in `tests/test_role_matrix.py`, which fails on any
route that is neither floored nor on that list."""

from __future__ import annotations

import uuid
from contextvars import ContextVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import Forbidden, NotFound
from app.config import settings
from app.models.assistant import Assistant, AssistantEditor
from app.models.enums import MemberRole
from app.models.membership import Membership
from app.models.organization import Organization

#: The lowest role that uses the Studio. Read at call time, so a test can
#: raise it to stand in for a role that does not exist yet.
STUDIO_FLOOR = MemberRole.member

#: Whether this request's session passed a second factor (Phase 8.4). Set by
#: `deps.get_current_user` from the token, for this request only.
strong_session: ContextVar[bool] = ContextVar("strong_session", default=False)


def admin_powers_held(membership: Membership) -> bool:
    """An owner or admin whose session hasn't passed a second factor, where
    the server requires one (`REQUIRE_ADMIN_2FA`)."""
    return (
        settings.require_admin_2fa
        and membership.role.satisfies(MemberRole.admin)
        and not strong_session.get()
    )


def _as_builder(membership: Membership) -> Membership:
    """The same membership as a builder, for this request: an unsaved copy,
    so nothing written back can change the real role."""
    return Membership(
        id=membership.id,
        org_id=membership.org_id,
        user_id=membership.user_id,
        role=MemberRole.member,
        created_at=membership.created_at,
        deactivated_at=membership.deactivated_at,
    )


def admit(membership: Membership) -> Membership:
    """The membership, if its role reaches the Studio; otherwise refused.

    Forbidden rather than not found: the caller does belong to this org, so
    there is nothing to hide about whether it exists."""
    if not membership.role.satisfies(STUDIO_FLOOR):
        raise Forbidden(
            "Your role doesn't include building assistants. Ask an admin if you need it.",
            code="studio_access_required",
        )
    return membership


async def member_of(
    session: AsyncSession,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    missing: str,
    chat: bool = False,
) -> Membership:
    """The caller's membership in `org_id`, past the floor.

    `chat=True` lets a role below the floor through, the Employee role
    (Phase 8.1): only the Chat dependencies in `api/deps.py` pass it, so a
    route admits employees only by asking for Chat by name.

    Someone outside the org gets 404 with `missing` as the message, so an id
    from another org confirms nothing. So does someone an admin removed
    (Phase 7b.4): a deactivated membership reaches nothing, from their next
    request on. And nobody reaches an archived org (Phase 7b.8)."""
    membership = await session.scalar(
        select(Membership)
        .join(Organization, Organization.id == Membership.org_id)
        .where(
            Membership.org_id == org_id,
            Membership.user_id == user_id,
            Membership.deactivated_at.is_(None),
            Organization.archived_at.is_(None),
        )
    )
    if membership is None:
        raise NotFound(missing)
    if admin_powers_held(membership):
        # Every admin check below refuses on its own: no route has to
        # remember the second factor (Phase 8.4).
        membership = _as_builder(membership)
    return membership if chat else admit(membership)


async def can_edit_assistant(
    session: AsyncSession, assistant: Assistant, membership: Membership
) -> bool:
    """Who may edit an assistant: an admin, its creator, or a co-editor its
    creator or an admin named (Phase 7b.4). One rule, read by every route
    that edits an assistant or its evals.

    Never a role below the Studio floor (Phase 8.1): a builder demoted to
    employee keeps their name on what they built, and edits none of it,
    previews included."""
    if not membership.role.satisfies(STUDIO_FLOOR):
        return False
    if membership.role.satisfies(MemberRole.admin):
        return True
    if assistant.created_by is not None and assistant.created_by == membership.user_id:
        return True
    granted = await session.scalar(
        select(AssistantEditor.id).where(
            AssistantEditor.assistant_id == assistant.id,
            AssistantEditor.user_id == membership.user_id,
        )
    )
    return granted is not None


def can_manage_editors(assistant: Assistant, membership: Membership) -> bool:
    """Who names co-editors: the creator or an admin, not a co-editor."""
    return membership.role.satisfies(MemberRole.admin) or (
        assistant.created_by is not None and assistant.created_by == membership.user_id
    )


__all__ = ["STUDIO_FLOOR", "admit", "can_edit_assistant", "can_manage_editors", "member_of"]
