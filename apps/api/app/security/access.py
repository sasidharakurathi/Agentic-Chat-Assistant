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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import Forbidden, NotFound
from app.models.enums import MemberRole
from app.models.membership import Membership

#: The lowest role that uses the Studio. Read at call time, so a test can
#: raise it to stand in for a role that does not exist yet.
STUDIO_FLOOR = MemberRole.member


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
    session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, *, missing: str
) -> Membership:
    """The caller's membership in `org_id`, past the floor.

    Someone outside the org gets 404 with `missing` as the message, so an id
    from another org confirms nothing."""
    membership = await session.scalar(
        select(Membership).where(Membership.org_id == org_id, Membership.user_id == user_id)
    )
    if membership is None:
        raise NotFound(missing)
    return admit(membership)


__all__ = ["STUDIO_FLOOR", "admit", "member_of"]
