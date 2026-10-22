from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import ratelimit
from app.api.deps import ClientIP, CurrentUser, SessionDep, per_ip_limit
from app.api.errors import BadRequest, Unauthorized
from app.config import settings
from app.models.membership import Membership
from app.models.organization import Organization
from app.models.user import User
from app.models.user_token import TokenPurpose
from app.schemas.auth import (
    DeviceCodeIn,
    DeviceCodeOut,
    EmailVerify,
    LoginOut,
    LoginRequest,
    MembershipOut,
    MeResponse,
    PasswordChange,
    PasswordResetConfirm,
    PasswordResetRequest,
    RefreshRequest,
    RegisterRequest,
    SignInMethods,
    TokenPair,
    TwoFactorFinish,
    TwoFactorStatus,
    UserOut,
)
from app.schemas.common import Message
from app.security.access import strong_session
from app.security.tokens import TokenError, decode_refresh_token
from app.services import account, audit, google_accounts, two_factor
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])

#: Signing up and signing in, per client IP (task 5.8).
AUTH = per_ip_limit("auth", "rate_limit_auth")
#: Sign-up's own bucket (Phase 8.4): a wave of new people doesn't use up
#: everyone's sign-ins.
SIGNUP = per_ip_limit("signup", "rate_limit_signup")


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
    dependencies=[SIGNUP],
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


@router.post("/login", response_model=LoginOut, dependencies=[AUTH])
async def login(
    body: LoginRequest, session: SessionDep, request: Request, ip: ClientIP
) -> LoginOut:
    # Guesses at one account from one address, before the password is
    # checked. Keyed by both, so an attacker elsewhere can't lock the real
    # user out; and a ceiling per account, from anywhere (Phase 8.4), so
    # it is the person's limit and not the office address's.
    email = body.email.lower()
    await ratelimit.enforce("login", f"{ip}|{email}", settings.rate_limit_login)
    await ratelimit.enforce("login_account", email, settings.rate_limit_login_account)
    try:
        user = await auth_service.authenticate(session, email=body.email, password=body.password)
    except Unauthorized as exc:
        await _sign_in_record(session, "user.login_failed", body.email, ip, exc.code)
        raise
    if await two_factor.enabled(session, user.id):
        # The password was right; the code finishes it (Phase 8.4).
        await session.commit()
        return LoginOut(two_factor_challenge=two_factor.challenge(user))
    await _sign_in_record(session, "user.login", body.email, ip)
    tokens = await auth_service.issue_tokens(
        session, user_id=user.id, user_agent=request.headers.get("user-agent"), ip=ip
    )
    return LoginOut(**_pair(tokens).model_dump())


@router.post("/login/two-factor", response_model=TokenPair, dependencies=[AUTH])
async def login_two_factor(
    body: TwoFactorFinish, session: SessionDep, request: Request, ip: ClientIP
) -> TokenPair:
    """The sign-in's second step: the challenge, and a code from the app
    or a recovery code."""
    user_id = two_factor.challenged_user(body.challenge)
    await ratelimit.enforce("two_factor", str(user_id), settings.rate_limit_two_factor)
    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise Unauthorized("Start again with your password", code="challenge_expired")
    method = await two_factor.check(session, user, body.code)
    if method is None:
        await _sign_in_record(session, "user.login_failed", user.email, ip, "two_factor")
        raise Unauthorized("That code isn't right", code="two_factor_code")
    await _sign_in_record(session, "user.login", user.email, ip, meta={"two_factor": method})
    tokens = await auth_service.issue_tokens(
        session,
        user_id=user.id,
        user_agent=request.headers.get("user-agent"),
        ip=ip,
        strong=True,
    )
    return _pair(tokens)


@router.post("/device-code", response_model=DeviceCodeOut)
async def make_device_code(user: CurrentUser, session: SessionDep, ip: ClientIP) -> DeviceCodeOut:
    """A code to sign in on another device (Phase 8.6): typed into that
    device's sign-in page within five minutes, once. A newer one voids the
    older. Whoever has the code signs in as you, so the page says never to
    give it to anyone."""
    code = account.new_device_code()
    await audit.record(
        session,
        action="user.device_code.create",
        actor_user_id=user.id,
        target_type="user",
        target_id=user.id,
        ip=ip,
    )
    await account.issue(session, user, TokenPurpose.device_code, raw=code)
    return DeviceCodeOut(code=code, expires_at=datetime.now(UTC) + account.DEVICE_CODE_TTL)


@router.post("/device-code/redeem", response_model=LoginOut, dependencies=[AUTH])
async def redeem_device_code(
    body: DeviceCodeIn, session: SessionDep, request: Request, ip: ClientIP
) -> LoginOut:
    """Sign in with a code from a signed-in device. It proves who you are,
    not a second factor: an account with an authenticator finishes with a
    code from it, as after a password."""
    try:
        user = await account.take(session, account.normal_code(body.code), TokenPurpose.device_code)
    except BadRequest:
        raise BadRequest(
            "That code has been used, has expired or isn't right. "
            "Make a new one on the device where you're signed in",
            code="device_code_expired",
        ) from None
    if await two_factor.enabled(session, user.id):
        await session.commit()
        return LoginOut(two_factor_challenge=two_factor.challenge(user))
    await _sign_in_record(session, "user.login", user.email, ip, meta={"method": "device_code"})
    tokens = await auth_service.issue_tokens(
        session, user_id=user.id, user_agent=request.headers.get("user-agent"), ip=ip
    )
    return LoginOut(**_pair(tokens).model_dump())


async def _sign_in_record(
    session: AsyncSession,
    action: str,
    email: str,
    ip: str | None,
    reason: str | None = None,
    *,
    meta: dict[str, str] | None = None,
) -> None:
    """Sign-ins, and failed ones, on the record (Phase 7b.7), filed under
    the person's organisation. A failure for an address with no account has
    no organisation to file it under: it is in the API's log only."""
    user = await session.scalar(select(User).where(User.email == email.strip().lower()))
    if user is None:
        return
    org_id = await session.scalar(
        select(Membership.org_id)
        .join(Organization, Organization.id == Membership.org_id)
        .where(
            Membership.user_id == user.id,
            Membership.deactivated_at.is_(None),
            Organization.archived_at.is_(None),
        )
        .order_by(Organization.is_personal, Membership.created_at)
        .limit(1)
    )
    await audit.record(
        session,
        action=action,
        org_id=org_id,
        actor_user_id=user.id,
        target_type="user",
        target_id=user.id,
        meta={**({"reason": reason} if reason else {}), **(meta or {})},
        ip=ip,
    )
    await session.commit()


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


# ── getting back in (Phase 7b.6) ─────────────────────────────


@router.post(
    "/password-reset",
    response_model=Message,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[per_ip_limit("account_mail_ip", "rate_limit_account_mail_ip")],
)
async def password_reset(body: PasswordResetRequest, session: SessionDep, ip: ClientIP) -> Message:
    """Email a reset link if an account has this address. The same answer
    either way: the form tells nobody who has an account."""
    await ratelimit.enforce("account_mail", body.email.lower(), settings.rate_limit_account_mail)
    await account.request_reset(session, email=body.email, ip=ip)
    return Message(
        message="If an account has that address, a link to reset its password is on its way."
    )


@router.post("/password-reset/confirm", response_model=Message, dependencies=[AUTH])
async def password_reset_confirm(
    body: PasswordResetConfirm, session: SessionDep, ip: ClientIP
) -> Message:
    """Set a new password with the emailed link. Every session ends; sign in
    with the new password."""
    await account.confirm_reset(session, token=body.token, new_password=body.new_password, ip=ip)
    return Message(message="Your password is changed. Sign in with it.")


@router.post("/password", response_model=TokenPair, dependencies=[AUTH])
async def change_password(
    body: PasswordChange, user: CurrentUser, session: SessionDep, request: Request, ip: ClientIP
) -> TokenPair:
    """Change it while signed in. Every other session ends; this one gets
    a fresh pair."""
    await account.change_password(
        session, user, current=body.current_password, new=body.new_password, ip=ip
    )
    tokens = await auth_service.issue_tokens(
        session, user_id=user.id, user_agent=request.headers.get("user-agent"), ip=ip
    )
    return _pair(tokens)


@router.post(
    "/verify-email/send",
    response_model=Message,
    dependencies=[per_ip_limit("account_mail_ip", "rate_limit_account_mail_ip")],
)
async def send_verification(user: CurrentUser, session: SessionDep) -> Message:
    await ratelimit.enforce("account_mail", user.email, settings.rate_limit_account_mail)
    sent = await account.send_verification(session, user)
    return Message(
        message="A link to confirm your address is on its way."
        if sent
        else "Your address is already confirmed."
    )


@router.post("/verify-email", response_model=Message, dependencies=[AUTH])
async def verify_email(body: EmailVerify, session: SessionDep, ip: ClientIP) -> Message:
    """Open the emailed link: the address is confirmed. No sign-in needed:
    the link is the proof."""
    await account.verify_email(session, token=body.token, ip=ip)
    return Message(message="Your address is confirmed.")


@router.post("/logout", response_model=Message)
async def logout(body: RefreshRequest, session: SessionDep, ip: ClientIP) -> Message:
    try:
        user_id = decode_refresh_token(body.refresh_token).user_id
    except TokenError:
        user_id = None
    await auth_service.logout(session, token=body.refresh_token)
    if user_id is not None:
        user = await session.get(User, user_id)
        if user is not None:
            await _sign_in_record(session, "user.logout", user.email, ip)
    return Message(message="logged out")


@router.get("/me", response_model=MeResponse)
async def me(user: CurrentUser, session: SessionDep) -> MeResponse:
    memberships = await session.scalars(
        select(Membership)
        .join(Organization, Organization.id == Membership.org_id)
        .where(
            Membership.user_id == user.id,
            Membership.deactivated_at.is_(None),
            Organization.archived_at.is_(None),
        )
    )
    return MeResponse(
        user=UserOut.model_validate(user),
        memberships=[MembershipOut(org_id=m.org_id, role=m.role.value) for m in memberships],
        sign_in=SignInMethods(
            password=user.password_hash is not None,
            google=await google_accounts.linked_email(session, user.id),
        ),
        two_factor=await _two_factor_status(session, user),
    )


async def _two_factor_status(session: AsyncSession, user: User) -> TwoFactorStatus:
    return TwoFactorStatus(
        enabled=await two_factor.enabled(session, user.id),
        recovery_codes_left=await two_factor.recovery_left(session, user.id),
        session_verified=strong_session.get(),
        required=settings.require_admin_2fa
        and await two_factor.is_admin_anywhere(session, user.id),
    )
