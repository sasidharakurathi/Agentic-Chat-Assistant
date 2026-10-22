"""Two-factor sign-in (Phase 8.4): an authenticator app, and recovery codes.

- **Setting it up** while signed in: a new secret, sealed with the KEK,
  shown once as a QR code; it signs in nothing until a code from the app
  confirms it. Confirming gives ten recovery codes, shown once, stored
  hashed, each good once.
- **Signing in** with a password: an account that has it gets a challenge
  instead of tokens (`challenge`, five minutes), and the code finishes the
  sign-in. A code works once (`last_step`), a recovery code once.
- **Admins**: with `REQUIRE_ADMIN_2FA`, an owner or admin acts as an admin
  only in a session that passed a second factor (`security/access`): an
  authenticator code, or a Google sign-in limited to the company's
  Workspace domain, where Google's own 2-step verification applies.
- **Locked out**: the operator clears it on the server
  (`python -m scripts.reset_two_factor`), recorded in the audit log.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

import jwt
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import BadRequest, Conflict, Unauthorized
from app.config import settings
from app.models.enums import MemberRole
from app.models.membership import Membership
from app.models.second_factor import SEALED_KIND, UserRecoveryCode, UserSecondFactor
from app.models.user import User
from app.security import totp
from app.security.crypto import SealedSecret, open_sealed, seal
from app.services import audit

RECOVERY_CODES = 10
CHALLENGE_TTL = timedelta(minutes=5)

Method = Literal["totp", "recovery"]


def _hash(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _normal(code: str) -> str:
    """A recovery code as typed: case, spaces and dashes don't matter."""
    return "".join(ch for ch in code.lower() if ch.isalnum())


async def _factor(session: AsyncSession, user_id: uuid.UUID) -> UserSecondFactor | None:
    return await session.scalar(select(UserSecondFactor).where(UserSecondFactor.user_id == user_id))


def _secret(row: UserSecondFactor) -> str:
    sealed = SealedSecret(ciphertext=row.ciphertext, dek_wrapped=row.dek_wrapped, nonce=row.nonce)
    return open_sealed(sealed, kind=SEALED_KIND)


async def enabled(session: AsyncSession, user_id: uuid.UUID) -> bool:
    row = await _factor(session, user_id)
    return row is not None and row.enabled_at is not None


async def recovery_left(session: AsyncSession, user_id: uuid.UUID) -> int:
    return int(
        await session.scalar(
            select(func.count())
            .select_from(UserRecoveryCode)
            .where(UserRecoveryCode.user_id == user_id, UserRecoveryCode.used_at.is_(None))
        )
        or 0
    )


async def is_admin_anywhere(session: AsyncSession, user_id: uuid.UUID) -> bool:
    found = await session.scalar(
        select(Membership.id)
        .where(
            Membership.user_id == user_id,
            Membership.deactivated_at.is_(None),
            Membership.role.in_((MemberRole.admin, MemberRole.owner)),
        )
        .limit(1)
        .execution_options(all_orgs=True)
    )
    return found is not None


# ── setting it up ────────────────────────────────────────────


async def begin(session: AsyncSession, user: User) -> tuple[str, str]:
    """A new secret for the app (replacing any setup not yet confirmed):
    the secret, and what the QR code holds."""
    row = await _factor(session, user.id)
    if row is not None and row.enabled_at is not None:
        raise Conflict("Two-step sign-in is already on", code="two_factor_on")
    secret = totp.new_secret()
    sealed = seal(secret, kind=SEALED_KIND)
    if row is None:
        row = UserSecondFactor(
            user_id=user.id, last_step=0, ciphertext=b"", dek_wrapped=b"", nonce=b""
        )
        session.add(row)
    row.ciphertext, row.dek_wrapped, row.nonce = sealed.ciphertext, sealed.dek_wrapped, sealed.nonce
    row.last_step = 0
    await session.commit()
    return secret, totp.uri(secret, account=user.email, issuer=settings.app_name)


async def _new_recovery_codes(session: AsyncSession, user_id: uuid.UUID) -> list[str]:
    await session.execute(delete(UserRecoveryCode).where(UserRecoveryCode.user_id == user_id))
    codes = []
    for _ in range(RECOVERY_CODES):
        raw = secrets.token_hex(5)  # ten characters, shown as xxxxx-xxxxx
        codes.append(f"{raw[:5]}-{raw[5:]}")
        session.add(UserRecoveryCode(user_id=user_id, code_hash=_hash(_normal(raw))))
    await session.flush()
    return codes


async def confirm(session: AsyncSession, user: User, code: str, *, ip: str | None) -> list[str]:
    """Turn it on with a code from the app. Returns the recovery codes,
    which are shown this once."""
    row = await _factor(session, user.id)
    if row is None or row.enabled_at is not None:
        raise BadRequest("Start the setup first", code="two_factor_not_started")
    step = totp.verify(_secret(row), code, after_step=row.last_step)
    if step is None:
        raise BadRequest(
            "That code isn't right. Check the app's clock, then try the next one",
            code="two_factor_code",
        )
    row.last_step = step
    row.enabled_at = datetime.now(UTC)
    codes = await _new_recovery_codes(session, user.id)
    await audit.record(
        session,
        action="user.two_factor.enable",
        actor_user_id=user.id,
        target_type="user",
        target_id=user.id,
        ip=ip,
    )
    await session.commit()
    return codes


# ── checking a code ──────────────────────────────────────────


async def check(session: AsyncSession, user: User, code: str) -> Method | None:
    """A code from the app or a recovery code, each good once. None: wrong."""
    row = await _factor(session, user.id)
    if row is None or row.enabled_at is None:
        return None
    step = totp.verify(_secret(row), code, after_step=row.last_step)
    if step is not None:
        # Claimed in one conditional update: two requests with the same
        # code can't both pass.
        claimed = await session.execute(
            update(UserSecondFactor)
            .where(UserSecondFactor.id == row.id, UserSecondFactor.last_step < step)
            .values(last_step=step)
            .execution_options(synchronize_session=False)
        )
        return "totp" if claimed.rowcount == 1 else None  # type: ignore[attr-defined]
    normal = _normal(code)
    if not normal:
        return None
    used = await session.execute(
        update(UserRecoveryCode)
        .where(
            UserRecoveryCode.user_id == user.id,
            UserRecoveryCode.code_hash == _hash(normal),
            UserRecoveryCode.used_at.is_(None),
        )
        .values(used_at=datetime.now(UTC))
        .execution_options(synchronize_session=False)
    )
    return "recovery" if used.rowcount == 1 else None  # type: ignore[attr-defined]


async def require_code(session: AsyncSession, user: User, code: str) -> Method:
    method = await check(session, user, code)
    if method is None:
        raise BadRequest("That code isn't right", code="two_factor_code")
    return method


async def disable(session: AsyncSession, user: User, code: str, *, ip: str | None) -> None:
    method = await require_code(session, user, code)
    await session.execute(delete(UserSecondFactor).where(UserSecondFactor.user_id == user.id))
    await session.execute(delete(UserRecoveryCode).where(UserRecoveryCode.user_id == user.id))
    await audit.record(
        session,
        action="user.two_factor.disable",
        actor_user_id=user.id,
        target_type="user",
        target_id=user.id,
        meta={"with": method},
        ip=ip,
    )
    await session.commit()


async def regenerate(session: AsyncSession, user: User, code: str, *, ip: str | None) -> list[str]:
    await require_code(session, user, code)
    codes = await _new_recovery_codes(session, user.id)
    await audit.record(
        session,
        action="user.two_factor.recovery_codes",
        actor_user_id=user.id,
        target_type="user",
        target_id=user.id,
        ip=ip,
    )
    await session.commit()
    return codes


async def reset(session: AsyncSession, user: User) -> None:
    """The operator's way out for someone locked out: clears it."""
    await session.execute(delete(UserSecondFactor).where(UserSecondFactor.user_id == user.id))
    await session.execute(delete(UserRecoveryCode).where(UserRecoveryCode.user_id == user.id))
    await audit.record(
        session,
        action="user.two_factor.reset",
        target_type="user",
        target_id=user.id,
        meta={"by": "operator"},
    )
    await session.commit()


# ── the sign-in's second step ────────────────────────────────


def challenge(user: User) -> str:
    """Proof that the password was right, good for five minutes, to be
    finished with a code. Signed: a client can't make one."""
    payload = {
        "typ": "two_factor",
        "sub": str(user.id),
        "jti": secrets.token_hex(8),
        "exp": datetime.now(UTC) + CHALLENGE_TTL,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def challenged_user(token: str) -> uuid.UUID:
    try:
        data = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise Unauthorized(
            "The sign-in took too long. Start again with your password", code="challenge_expired"
        ) from exc
    if data.get("typ") != "two_factor" or not data.get("sub"):
        raise Unauthorized("Start again with your password", code="challenge_expired")
    return uuid.UUID(str(data["sub"]))


__all__ = [
    "RECOVERY_CODES",
    "begin",
    "challenge",
    "challenged_user",
    "check",
    "confirm",
    "disable",
    "enabled",
    "is_admin_anywhere",
    "recovery_left",
    "regenerate",
    "require_code",
    "reset",
]
