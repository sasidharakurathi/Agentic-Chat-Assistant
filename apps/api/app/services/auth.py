"""Authentication: registration, login, refresh-token rotation with reuse detection."""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import Conflict, Unauthorized
from app.config import settings
from app.logging import get_logger
from app.models.enums import MemberRole
from app.models.membership import Membership
from app.models.organization import Organization
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.security.passwords import hash_password, needs_rehash, verify_password
from app.security.tokens import (
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
)
from app.services import audit
from app.services.slug import slugify

log = get_logger(__name__)


@dataclass(frozen=True)
class IssuedTokens:
    access_token: str
    refresh_token: str
    expires_in: int


async def _unique_org_slug(session: AsyncSession, name: str) -> str:
    base = slugify(name)[:100]
    candidate = base
    for _ in range(6):
        exists = await session.scalar(select(Organization.id).where(Organization.slug == candidate))
        if exists is None:
            return candidate
        candidate = f"{base}-{secrets.token_hex(3)}"
    return f"{base}-{secrets.token_hex(6)}"


async def register(
    session: AsyncSession, *, email: str, password: str, name: str, ip: str | None = None
) -> User:
    email = email.strip().lower()
    if await session.scalar(select(User.id).where(User.email == email)) is not None:
        raise Conflict("An account with that email already exists", code="email_taken")

    user = User(email=email, password_hash=hash_password(password), name=name.strip())
    session.add(user)
    await session.flush()

    org_name = f"{name.strip() or email.split('@')[0]}'s Org"
    org = Organization(
        name=org_name,
        slug=await _unique_org_slug(session, org_name),
        is_personal=True,
    )
    session.add(org)
    await session.flush()

    session.add(Membership(org_id=org.id, user_id=user.id, role=MemberRole.owner))
    await audit.record(
        session,
        action="user.register",
        org_id=org.id,
        actor_user_id=user.id,
        target_type="user",
        target_id=user.id,
        ip=ip,
    )
    await session.flush()
    log.info("user_registered", user_id=str(user.id), org_id=str(org.id))
    return user


async def authenticate(session: AsyncSession, *, email: str, password: str) -> User:
    email = email.strip().lower()
    user = await session.scalar(select(User).where(User.email == email))
    if user is None or not verify_password(password, user.password_hash):
        raise Unauthorized("Invalid email or password", code="invalid_credentials")
    if not user.is_active:
        raise Unauthorized("Account is disabled", code="account_disabled")

    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    user.last_login_at = datetime.now(UTC)
    await session.flush()
    return user


async def issue_tokens(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    family_id: uuid.UUID | None = None,
    user_agent: str | None = None,
    ip: str | None = None,
) -> IssuedTokens:
    access, _ = create_access_token(user_id)
    refresh, refresh_claims = create_refresh_token(user_id, family_id=family_id)
    session.add(
        RefreshToken(
            user_id=user_id,
            jti=refresh_claims.jti,
            family_id=refresh_claims.family_id,
            expires_at=refresh_claims.expires_at,
            user_agent=(user_agent or "")[:400] or None,
            ip=ip,
        )
    )
    await session.flush()
    return IssuedTokens(
        access_token=access,
        refresh_token=refresh,
        expires_in=settings.jwt_access_ttl_seconds,
    )


async def rotate_refresh_token(
    session: AsyncSession, *, token: str, user_agent: str | None = None, ip: str | None = None
) -> IssuedTokens:
    try:
        claims = decode_refresh_token(token)
    except TokenError as exc:
        raise Unauthorized("Invalid refresh token", code="invalid_refresh") from exc

    row = await session.scalar(select(RefreshToken).where(RefreshToken.jti == claims.jti))
    if row is None:
        await _revoke_family(session, claims.family_id, reason="unknown_jti")
        # The request will unwind with an error; persist the revocation regardless.
        await session.commit()
        raise Unauthorized("Refresh token not recognized", code="invalid_refresh")

    if not row.is_active:
        await _revoke_family(session, row.family_id, reason="reuse_detected")
        log.warning("refresh_reuse_detected", user_id=str(row.user_id), family=str(row.family_id))
        await session.commit()
        raise Unauthorized("Refresh token already used", code="refresh_reused")

    if row.expires_at <= datetime.now(UTC):
        raise Unauthorized("Refresh token expired", code="refresh_expired")

    row.used_at = datetime.now(UTC)
    await session.flush()
    return await issue_tokens(
        session, user_id=row.user_id, family_id=row.family_id, user_agent=user_agent, ip=ip
    )


async def _revoke_family(session: AsyncSession, family_id: uuid.UUID, *, reason: str) -> None:
    rows = await session.scalars(
        select(RefreshToken).where(
            RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None)
        )
    )
    now = datetime.now(UTC)
    for r in rows:
        r.revoked_at = now
    await session.flush()
    log.info("refresh_family_revoked", family=str(family_id), reason=reason)


async def logout(session: AsyncSession, *, token: str) -> None:
    try:
        claims = decode_refresh_token(token)
    except TokenError:
        return
    await _revoke_family(session, claims.family_id, reason="logout")


__all__ = [
    "IssuedTokens",
    "authenticate",
    "issue_tokens",
    "logout",
    "register",
    "rotate_refresh_token",
]
