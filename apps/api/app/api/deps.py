"""FastAPI dependencies: auth, current user, org scoping + RBAC."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Header, Path, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import Forbidden, NotFound, Unauthorized
from app.db.session import get_session
from app.models.enums import MemberRole
from app.models.membership import Membership
from app.models.user import User
from app.security.tokens import TokenError, decode_access_token

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


ClientIP = Annotated[str | None, Depends(client_ip)]


async def get_current_user(
    session: SessionDep,
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise Unauthorized("Missing bearer token", code="missing_token")
    token = authorization.split(" ", 1)[1].strip()
    try:
        claims = decode_access_token(token)
    except TokenError as exc:
        raise Unauthorized("Invalid or expired token", code="invalid_token") from exc

    user = await session.get(User, claims.user_id)
    if user is None or not user.is_active:
        raise Unauthorized("User not found or inactive", code="invalid_token")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_org_membership(
    session: SessionDep,
    user: CurrentUser,
    org_id: Annotated[uuid.UUID, Path()],
) -> Membership:
    membership = await session.scalar(
        select(Membership).where(Membership.org_id == org_id, Membership.user_id == user.id)
    )
    if membership is None:
        # Don't disclose whether the org exists.
        raise NotFound("Organization not found")
    return membership


OrgMembership = Annotated[Membership, Depends(get_org_membership)]


def require_role(
    minimum: MemberRole,
) -> Callable[[Membership], Awaitable[Membership]]:
    async def _dep(membership: OrgMembership) -> Membership:
        if not membership.role.satisfies(minimum):
            raise Forbidden(f"Requires {minimum.value} role or higher", code="insufficient_role")
        return membership

    return _dep


__all__ = [
    "ClientIP",
    "CurrentUser",
    "OrgMembership",
    "SessionDep",
    "client_ip",
    "get_current_user",
    "get_org_membership",
    "require_role",
]
