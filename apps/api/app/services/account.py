"""Getting back in, changing a password, proving an address (Phase 7b.6).

- **Password reset.** Asked for by address; the answer is the same whether
  or not an account has it, so the form tells nobody who has one. The
  emailed link works once, for `PASSWORD_RESET_TTL_MINUTES`, and a newer one
  voids it. Using it sets the new password, ends every session (each
  refresh token is revoked, and access tokens issued before the change stop
  working: `deps.get_current_user`), and emails a notice saying it happened.
- **Change password** while signed in: the current password first. Other
  sessions end; this one gets a fresh pair.
- **Email verification.** A link to the address; opening it records that
  the address reaches its owner. Joining through an invite to the address
  counts too.

Links carry their token after `#`, which a browser never sends to a server:
it stays out of the proxy's and the API's logs, and out of the Referer.
Tokens are stored hashed, like invites.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import BadRequest, Unauthorized
from app.config import settings
from app.logging import get_logger
from app.mail import templates
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.models.user_token import TokenPurpose, UserToken
from app.security.passwords import hash_password, verify_password
from app.services import audit, mail

log = get_logger(__name__)


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def link(path: str, raw: str) -> str:
    """The page, with the token after `#`: never sent to any server."""
    return f"{templates.app_link(path)}#token={raw}"


#: How long a device code works (Phase 8.6): long enough to pick up the
#: other device and type it.
DEVICE_CODE_TTL = timedelta(minutes=5)
#: A device code's letters (Phase 8.6): no 0/O or 1/I/L to misread.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8


def new_device_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def normal_code(text: str) -> str:
    """A code as typed ("abcd-efgh ", "ABCD EFGH"), as it was made."""
    return "".join(ch for ch in text.upper() if ch.isalnum())


async def issue(
    session: AsyncSession, user: User, purpose: TokenPurpose, raw: str | None = None
) -> str:
    """A fresh one-time token for `user`; any still open for the same
    purpose is voided. Committed. Returns the raw token, shown only in the
    link (or, for a device code, on the screen)."""
    now = datetime.now(UTC)
    await session.execute(
        update(UserToken)
        .where(
            UserToken.user_id == user.id,
            UserToken.purpose == purpose,
            UserToken.used_at.is_(None),
        )
        .values(used_at=now)
        .execution_options(synchronize_session=False)
    )
    ttl = {
        TokenPurpose.password_reset: timedelta(minutes=settings.password_reset_ttl_minutes),
        TokenPurpose.verify_email: timedelta(hours=settings.verify_email_ttl_hours),
        TokenPurpose.google_handoff: timedelta(seconds=60),
        TokenPurpose.device_code: DEVICE_CODE_TTL,
    }[purpose]
    raw = raw or secrets.token_urlsafe(32)
    session.add(
        UserToken(user_id=user.id, purpose=purpose, token_hash=_hash(raw), expires_at=now + ttl)
    )
    await session.commit()
    return raw


async def _consume(session: AsyncSession, raw: str, purpose: TokenPurpose) -> User:
    """The token's user, the token used up in one conditional update so it
    works exactly once. One refusal for unknown, used and expired alike."""
    now = datetime.now(UTC)
    row = await session.scalar(
        select(UserToken).where(UserToken.token_hash == _hash(raw), UserToken.purpose == purpose)
    )
    refused = BadRequest(
        "This link has been used or has expired. Ask for a new one", code="link_expired"
    )
    if row is None or row.used_at is not None or row.expires_at <= now:
        raise refused
    claimed = await session.execute(
        update(UserToken)
        .where(UserToken.id == row.id, UserToken.used_at.is_(None))
        .values(used_at=now)
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:  # type: ignore[attr-defined]
        raise refused
    user = await session.get(User, row.user_id)
    if user is None or not user.is_active:
        raise refused
    return user


async def take(session: AsyncSession, raw: str, purpose: TokenPurpose) -> User:
    """Use a one-time token: its user, the token spent. For flows outside
    this module (Google's handoff, Phase 8.3)."""
    return await _consume(session, raw, purpose)


async def _end_sessions(session: AsyncSession, user: User, now: datetime) -> int:
    result = await session.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


async def _set_password(session: AsyncSession, user: User, new: str, now: datetime) -> int:
    user.password_hash = hash_password(new)
    # To the microsecond, as access tokens carry their issue time (`iat_us`):
    # a token from earlier in the same second is before the change, and the
    # fresh pair `change_password` hands back is after it.
    user.password_changed_at = now
    return await _end_sessions(session, user, now)


# ── reset ────────────────────────────────────────────────────


async def request_reset(session: AsyncSession, *, email: str, ip: str | None = None) -> None:
    """Email a reset link if an active account has this address. Says
    nothing either way."""
    user = await session.scalar(select(User).where(User.email == email.strip().lower()))
    if user is None or not user.is_active:
        log.info("password_reset_unknown")
        return
    if user.password_hash is None:
        # Signs in with Google (Phase 8.3): a password would be a second way
        # in around Google's own checks. The same answer as ever, no email.
        log.info("password_reset_google_only")
        return
    raw = await issue(session, user, TokenPurpose.password_reset)
    await audit.record(
        session,
        action="user.password_reset_requested",
        actor_user_id=user.id,
        target_type="user",
        target_id=user.id,
        ip=ip,
    )
    await session.commit()
    await mail.send(
        templates.password_reset(
            to=user.email,
            link=link("/reset-password", raw),
            minutes=settings.password_reset_ttl_minutes,
        ),
        org_id=None,
    )


async def confirm_reset(
    session: AsyncSession, *, token: str, new_password: str, ip: str | None = None
) -> User:
    user = await _consume(session, token, TokenPurpose.password_reset)
    now = datetime.now(UTC)
    ended = await _set_password(session, user, new_password, now)
    # Opening the link proved the address, too.
    user.email_verified_at = user.email_verified_at or now
    await audit.record(
        session,
        action="user.password_reset",
        actor_user_id=user.id,
        target_type="user",
        target_id=user.id,
        meta={"sessions_ended": ended},
        ip=ip,
    )
    await session.commit()
    await mail.send(templates.password_changed(to=user.email), org_id=None)
    return user


# ── change ───────────────────────────────────────────────────


async def change_password(
    session: AsyncSession, user: User, *, current: str, new: str, ip: str | None = None
) -> None:
    """Every session ends, this one included; the route hands this one a
    fresh pair."""
    if user.password_hash is None or not verify_password(current, user.password_hash):
        raise Unauthorized("That isn't your current password", code="wrong_password")
    if current == new:
        raise BadRequest("Choose a password different from the current one", code="same_password")
    now = datetime.now(UTC)
    ended = await _set_password(session, user, new, now)
    await audit.record(
        session,
        action="user.password_change",
        actor_user_id=user.id,
        target_type="user",
        target_id=user.id,
        meta={"sessions_ended": ended},
        ip=ip,
    )
    await session.commit()
    await mail.send(templates.password_changed(to=user.email), org_id=None)


# ── verification ─────────────────────────────────────────────


async def send_verification(session: AsyncSession, user: User) -> bool:
    """Email an address-check link. False when the address is already
    verified, and nothing is sent."""
    if user.email_verified_at is not None:
        return False
    raw = await issue(session, user, TokenPurpose.verify_email)
    await mail.send(
        templates.verify_email(
            to=user.email,
            link=link("/verify-email", raw),
            hours=settings.verify_email_ttl_hours,
        ),
        org_id=None,
    )
    return True


async def verify_email(session: AsyncSession, *, token: str, ip: str | None = None) -> User:
    user = await _consume(session, token, TokenPurpose.verify_email)
    user.email_verified_at = user.email_verified_at or datetime.now(UTC)
    await audit.record(
        session,
        action="user.email_verified",
        actor_user_id=user.id,
        target_type="user",
        target_id=user.id,
        ip=ip,
    )
    await session.commit()
    return user


def issued_before_password_change(user: User, issued_at: datetime | None) -> bool:
    """A session token from before the password last changed."""
    if user.password_changed_at is None or issued_at is None:
        return False
    return issued_at < user.password_changed_at


__all__ = [
    "change_password",
    "confirm_reset",
    "issue",
    "issued_before_password_change",
    "link",
    "request_reset",
    "send_verification",
    "verify_email",
]
