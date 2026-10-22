"""Two-step sign-in, for the signed-in person (Phase 8.4): set it up, check
a code to open admin powers in this session, change the recovery codes,
turn it off. The rules are `services/two_factor`."""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.api import ratelimit
from app.api.deps import ClientIP, CurrentUser, SessionDep
from app.api.errors import Unauthorized
from app.config import settings
from app.schemas.auth import (
    RecoveryCodesOut,
    TokenPair,
    TwoFactorCode,
    TwoFactorConfirmed,
    TwoFactorSetupOut,
    TwoFactorStatus,
)
from app.schemas.common import Message
from app.security.access import strong_session
from app.services import audit, two_factor
from app.services import auth as auth_service

router = APIRouter(prefix="/auth/two-factor", tags=["auth"])


async def _limit(user_id: object) -> None:
    # Six digits must not be guessable: a few tries per person.
    await ratelimit.enforce("two_factor", str(user_id), settings.rate_limit_two_factor)


def _pair(tokens: auth_service.IssuedTokens) -> TokenPair:
    return TokenPair(
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        expires_in=tokens.expires_in,
    )


@router.get("", response_model=TwoFactorStatus)
async def status(user: CurrentUser, session: SessionDep) -> TwoFactorStatus:
    return TwoFactorStatus(
        enabled=await two_factor.enabled(session, user.id),
        recovery_codes_left=await two_factor.recovery_left(session, user.id),
        session_verified=strong_session.get(),
        required=settings.require_admin_2fa
        and await two_factor.is_admin_anywhere(session, user.id),
    )


@router.post("/setup", response_model=TwoFactorSetupOut)
async def setup(user: CurrentUser, session: SessionDep) -> TwoFactorSetupOut:
    """A new secret for the authenticator app. It does nothing until
    confirmed with a code from the app."""
    secret, uri = await two_factor.begin(session, user)
    return TwoFactorSetupOut(secret=secret, otpauth_uri=uri)


@router.post("/confirm", response_model=TwoFactorConfirmed)
async def confirm(
    body: TwoFactorCode, user: CurrentUser, session: SessionDep, request: Request, ip: ClientIP
) -> TwoFactorConfirmed:
    """Turn it on with a code from the app: the recovery codes, shown once,
    and a fresh session that has passed it."""
    await _limit(user.id)
    codes = await two_factor.confirm(session, user, body.code, ip=ip)
    tokens = await auth_service.issue_tokens(
        session,
        user_id=user.id,
        user_agent=request.headers.get("user-agent"),
        ip=ip,
        strong=True,
    )
    await session.commit()
    return TwoFactorConfirmed(recovery_codes=codes, tokens=_pair(tokens))


@router.post("/verify", response_model=TokenPair)
async def verify(
    body: TwoFactorCode, user: CurrentUser, session: SessionDep, request: Request, ip: ClientIP
) -> TokenPair:
    """A code, for a session that hasn't passed the second factor yet (one
    from before it was set up): a fresh session that has."""
    await _limit(user.id)
    method = await two_factor.check(session, user, body.code)
    if method is None:
        await session.commit()
        raise Unauthorized("That code isn't right", code="two_factor_code")
    await audit.record(
        session,
        action="user.two_factor.verify",
        actor_user_id=user.id,
        target_type="user",
        target_id=user.id,
        meta={"with": method},
        ip=ip,
    )
    tokens = await auth_service.issue_tokens(
        session,
        user_id=user.id,
        user_agent=request.headers.get("user-agent"),
        ip=ip,
        strong=True,
    )
    await session.commit()
    return _pair(tokens)


@router.post("/recovery-codes", response_model=RecoveryCodesOut)
async def recovery_codes(
    body: TwoFactorCode, user: CurrentUser, session: SessionDep, ip: ClientIP
) -> RecoveryCodesOut:
    """New recovery codes, with a code: the old ones stop working."""
    await _limit(user.id)
    return RecoveryCodesOut(
        recovery_codes=await two_factor.regenerate(session, user, body.code, ip=ip)
    )


@router.post("/disable", response_model=Message)
async def disable(
    body: TwoFactorCode, user: CurrentUser, session: SessionDep, ip: ClientIP
) -> Message:
    """Turn it off, with a code."""
    await _limit(user.id)
    await two_factor.disable(session, user, body.code, ip=ip)
    return Message(message="Two-step sign-in is off.")


__all__ = ["router"]
