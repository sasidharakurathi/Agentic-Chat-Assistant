"""Authentication: registration, login, refresh-token rotation with reuse detection."""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import cache

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import Conflict, Forbidden, Unauthorized
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


async def _may_register(session: AsyncSession, email: str, invite_token: str | None) -> bool:
    """Sign-up by invitation (task 6.6): the first account on a new install,
    or the holder of a live invite for this address.

    The token and not only the address: an invite is mailed to someone, and
    whoever merely knows that an address was invited must not be able to
    take the account before its owner does.
    """
    if settings.registration == "open":
        return True
    if await session.scalar(select(User.id).limit(1)) is None:
        return True
    if not invite_token:
        return False
    # Imported here: `orgs` is the invites' home and imports nothing of ours.
    from app.models.invite import Invite
    from app.services.orgs import _hash_token

    invite = await session.scalar(
        select(Invite).where(Invite.token_hash == _hash_token(invite_token))
    )
    return (
        invite is not None
        and invite.accepted_at is None
        and invite.expires_at > datetime.now(UTC)
        and invite.email == email
    )


async def register(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    name: str,
    ip: str | None = None,
    invite_token: str | None = None,
) -> User:
    email = email.strip().lower()
    if not await _may_register(session, email, invite_token):
        # One answer for no invite, a wrong one, a used one and an expired
        # one: which of those it was is not the asker's to learn.
        raise Forbidden(
            "Sign-up on this server is by invitation. Ask an admin of your team for an "
            "invite link, and open it to create your account.",
            code="registration_by_invite",
        )
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
    # Commit before returning, not in `get_session`'s teardown. The caller
    # gets an access token in this very response and will typically use it
    # immediately — and the dependency's commit runs *after* the response is
    # sent, so that next request can beat the row into existence and get a
    # 401 "User not found". Same race (and same fix) as the mutating
    # assistant/conversation services.
    await session.commit()
    log.info("user_registered", user_id=str(user.id), org_id=str(org.id))
    return user


@cache
def _decoy_hash() -> str:
    """A real hash of nothing anyone knows, to verify against when the
    address has no account."""
    return hash_password(secrets.token_urlsafe(24))


async def authenticate(session: AsyncSession, *, email: str, password: str) -> User:
    email = email.strip().lower()
    user = await session.scalar(select(User).where(User.email == email))
    # An unknown address still costs one password hash (task 6.3). It used
    # to return at once, so the response time said whether an account
    # exists.
    hashed = user.password_hash if user is not None else _decoy_hash()
    if not verify_password(password, hashed) or user is None:
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
        if await _retry_after_lost_reply(session, row):
            log.info("refresh_retry_accepted", user_id=str(row.user_id), family=str(row.family_id))
            return await issue_tokens(
                session, user_id=row.user_id, family_id=row.family_id, user_agent=user_agent, ip=ip
            )
        await _revoke_family(session, row.family_id, reason="reuse_detected")
        log.warning("refresh_reuse_detected", user_id=str(row.user_id), family=str(row.family_id))
        await session.commit()
        raise Unauthorized("Refresh token already used", code="refresh_reused")

    if row.expires_at <= datetime.now(UTC):
        raise Unauthorized("Refresh token expired", code="refresh_expired")

    # Claimed in one conditional UPDATE (task 6.3). Reading `used_at` and
    # then writing it let two requests with the same token both pass the
    # check above and both get a new pair.
    claimed = await session.execute(
        update(RefreshToken)
        .where(RefreshToken.id == row.id, RefreshToken.used_at.is_(None))
        .values(used_at=datetime.now(UTC))
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:  # type: ignore[attr-defined]
        await _revoke_family(session, row.family_id, reason="reuse_detected")
        await session.commit()
        raise Unauthorized("Refresh token already used", code="refresh_reused")
    return await issue_tokens(
        session, user_id=row.user_id, family_id=row.family_id, user_agent=user_agent, ip=ip
    )


async def _retry_after_lost_reply(session: AsyncSession, row: RefreshToken) -> bool:
    """Whether a used token presented again is a retry whose reply was lost
    (Phase 7a.8), and if so, retire the pair that reply carried.

    On a weak connection the rotation succeeds on the server and its reply
    never arrives, so the phone tries again with the token it still has.
    That used to read as theft and revoke the whole session. It is a retry
    when: the token was used within the grace window, its session is not
    revoked, and nothing newer in the session has been used since (the
    pair from the lost reply is unused, so it never reached anyone). Then
    that unused pair is revoked and the caller gets a new one in the same
    session. Anything else is still reuse: the session is revoked.
    """
    grace = settings.refresh_reuse_grace_s
    if not grace or row.revoked_at is not None or row.used_at is None:
        return False
    now = datetime.now(UTC)
    if now - row.used_at > timedelta(seconds=grace):
        return False
    moved_on = await session.scalar(
        select(RefreshToken.id).where(
            RefreshToken.family_id == row.family_id,
            RefreshToken.used_at.is_not(None),
            RefreshToken.used_at > row.used_at,
        )
    )
    if moved_on is not None:
        return False
    await session.execute(
        update(RefreshToken)
        .where(
            RefreshToken.family_id == row.family_id,
            RefreshToken.used_at.is_(None),
            RefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=now)
        .execution_options(synchronize_session=False)
    )
    return True


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
