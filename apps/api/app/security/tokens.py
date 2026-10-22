"""JWT encode/decode for access + refresh tokens.

Access tokens are stateless and short-lived. Refresh tokens carry a ``jti`` and
``family`` and are checked against the ``refresh_tokens`` table (rotation + reuse
detection) in :mod:`app.services.auth`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt

from app.config import settings

TokenType = Literal["access", "refresh"]


class TokenError(Exception):
    """Raised when a token is missing, malformed, expired, or the wrong type."""


@dataclass(frozen=True)
class AccessTokenClaims:
    user_id: uuid.UUID
    jti: str
    expires_at: datetime
    #: When it was issued: one older than the last password change is refused.
    issued_at: datetime | None = None
    #: The sign-in passed a second factor (Phase 8.4).
    strong: bool = False


@dataclass(frozen=True)
class RefreshTokenClaims:
    user_id: uuid.UUID
    jti: str
    family_id: uuid.UUID
    expires_at: datetime


def _now() -> datetime:
    return datetime.now(UTC)


_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MICROSECOND = timedelta(microseconds=1)


def _micros(when: datetime) -> int:
    """`when` in whole microseconds since the epoch, exactly (no float)."""
    return (when - _EPOCH) // _MICROSECOND


def _issued_at(data: dict[str, Any]) -> datetime | None:
    # `iat` is whole seconds, too coarse to order a token against a
    # password change in the same second (Phase 7b.6), so access tokens
    # also carry `iat_us`. A token from before it has only `iat`.
    if "iat_us" in data:
        return _EPOCH + timedelta(microseconds=int(data["iat_us"]))
    if "iat" in data:
        return datetime.fromtimestamp(data["iat"], tz=UTC)
    return None


def _encode(payload: dict[str, Any]) -> str:
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def _decode(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("token expired") from exc
    except jwt.PyJWTError as exc:
        raise TokenError("invalid token") from exc


def create_access_token(
    user_id: uuid.UUID, *, strong: bool = False
) -> tuple[str, AccessTokenClaims]:
    jti = uuid.uuid4().hex
    now = _now()
    exp = now + timedelta(seconds=settings.jwt_access_ttl_seconds)
    token = _encode(
        {
            "sub": str(user_id),
            "type": "access",
            "jti": jti,
            "iat": now,
            "iat_us": _micros(now),
            "exp": exp,
            # Signed like the rest: a client can't claim it.
            "mfa": strong,
        }
    )
    return token, AccessTokenClaims(
        user_id=user_id, jti=jti, expires_at=exp, issued_at=now, strong=strong
    )


def create_refresh_token(
    user_id: uuid.UUID, family_id: uuid.UUID | None = None
) -> tuple[str, RefreshTokenClaims]:
    jti = uuid.uuid4().hex
    family = family_id or uuid.uuid4()
    now = _now()
    exp = now + timedelta(seconds=settings.jwt_refresh_ttl_seconds)
    token = _encode(
        {
            "sub": str(user_id),
            "type": "refresh",
            "jti": jti,
            "fam": str(family),
            "iat": now,
            "exp": exp,
        }
    )
    return token, RefreshTokenClaims(user_id=user_id, jti=jti, family_id=family, expires_at=exp)


def decode_access_token(token: str) -> AccessTokenClaims:
    data = _decode(token)
    if data.get("type") != "access":
        raise TokenError("expected an access token")
    return AccessTokenClaims(
        user_id=uuid.UUID(data["sub"]),
        jti=data["jti"],
        expires_at=datetime.fromtimestamp(data["exp"], tz=UTC),
        issued_at=_issued_at(data),
        strong=data.get("mfa") is True,
    )


def decode_refresh_token(token: str) -> RefreshTokenClaims:
    data = _decode(token)
    if data.get("type") != "refresh":
        raise TokenError("expected a refresh token")
    return RefreshTokenClaims(
        user_id=uuid.UUID(data["sub"]),
        jti=data["jti"],
        family_id=uuid.UUID(data["fam"]),
        expires_at=datetime.fromtimestamp(data["exp"], tz=UTC),
    )


__all__ = [
    "AccessTokenClaims",
    "RefreshTokenClaims",
    "TokenError",
    "create_access_token",
    "create_refresh_token",
    "decode_access_token",
    "decode_refresh_token",
]
