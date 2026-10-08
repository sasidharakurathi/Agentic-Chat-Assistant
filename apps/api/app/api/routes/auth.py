from __future__ import annotations

from fastapi import APIRouter, Request, status
from sqlalchemy import select

from app.api import ratelimit
from app.api.deps import ClientIP, CurrentUser, SessionDep, per_ip_limit
from app.config import settings
from app.models.membership import Membership
from app.schemas.auth import (
    LoginRequest,
    MembershipOut,
    MeResponse,
    RefreshRequest,
    RegisterRequest,
    TokenPair,
    UserOut,
)
from app.schemas.common import Message
from app.security.tokens import TokenError, decode_refresh_token
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])

#: Signing up and signing in, per client IP (task 5.8).
AUTH = per_ip_limit("auth", "rate_limit_auth")


def _pair(tokens: auth_service.IssuedTokens) -> TokenPair:
    return TokenPair(
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        expires_in=tokens.expires_in,
    )


@router.post(
    "/register",
    response_model=TokenPair,
    status_code=status.HTTP_201_CREATED,
    dependencies=[AUTH],
)
async def register(
    body: RegisterRequest, session: SessionDep, request: Request, ip: ClientIP
) -> TokenPair:
    user = await auth_service.register(
        session,
        email=body.email,
        password=body.password,
        name=body.name,
        ip=ip,
        invite_token=body.invite_token,
    )
    tokens = await auth_service.issue_tokens(
        session,
        user_id=user.id,
        user_agent=request.headers.get("user-agent"),
        ip=ip,
    )
    return _pair(tokens)


@router.post("/login", response_model=TokenPair, dependencies=[AUTH])
async def login(
    body: LoginRequest, session: SessionDep, request: Request, ip: ClientIP
) -> TokenPair:
    # Guesses at one account from one address, before the password is
    # checked. Keyed by both, so an attacker elsewhere can't lock the real
    # user out.
    await ratelimit.enforce("login", f"{ip}|{body.email.lower()}", settings.rate_limit_login)
    user = await auth_service.authenticate(session, email=body.email, password=body.password)
    tokens = await auth_service.issue_tokens(
        session, user_id=user.id, user_agent=request.headers.get("user-agent"), ip=ip
    )
    return _pair(tokens)


@router.post(
    "/refresh",
    response_model=TokenPair,
    dependencies=[per_ip_limit("refresh_ip", "rate_limit_refresh_ip")],
)
async def refresh(
    body: RefreshRequest, session: SessionDep, request: Request, ip: ClientIP
) -> TokenPair:
    # Per session, not per address (Phase 7a.8): everyone behind one VPN or
    # office NAT shares an IP, and a refresh that fails reads as a sign-out.
    # A token that doesn't verify is refused by the service below.
    try:
        family = decode_refresh_token(body.refresh_token).family_id
    except TokenError:
        family = None
    if family is not None:
        await ratelimit.enforce("refresh", str(family), settings.rate_limit_refresh)
    tokens = await auth_service.rotate_refresh_token(
        session, token=body.refresh_token, user_agent=request.headers.get("user-agent"), ip=ip
    )
    return _pair(tokens)


@router.post("/logout", response_model=Message)
async def logout(body: RefreshRequest, session: SessionDep) -> Message:
    await auth_service.logout(session, token=body.refresh_token)
    return Message(message="logged out")


@router.get("/me", response_model=MeResponse)
async def me(user: CurrentUser, session: SessionDep) -> MeResponse:
    memberships = await session.scalars(select(Membership).where(Membership.user_id == user.id))
    return MeResponse(
        user=UserOut.model_validate(user),
        memberships=[MembershipOut(org_id=m.org_id, role=m.role.value) for m in memberships],
    )
