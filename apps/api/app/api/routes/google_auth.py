"""Google sign-in's routes (Phase 8.3). The protocol is `services/google`,
the account rules `services/google_accounts`.

The browser goes: `start` (the state cookie is set, then Google) →
Google → `callback` (the code is redeemed and checked) → the web page
`/auth/google#code=...` → `exchange` (the page trades the one-minute code
for its session tokens). The tokens never travel in an address; the code
does, after `#`, which no server sees.
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import RedirectResponse

from app.api.deps import ClientIP, CurrentUser, SessionDep, per_ip_limit
from app.api.errors import NotFound
from app.config import settings
from app.logging import get_logger
from app.mail.templates import app_link
from app.models.user_token import TokenPurpose
from app.schemas.auth import GoogleExchange, LinkTicketOut, LoginOut, ProvidersOut
from app.schemas.common import Message
from app.services import account, google, google_accounts, two_factor
from app.services import auth as auth_service
from app.services.google import GoogleRefused

router = APIRouter(prefix="/auth", tags=["auth"])
log = get_logger(__name__)

AUTH = per_ip_limit("auth", "rate_limit_auth")
SIGNUP = per_ip_limit("signup", "rate_limit_signup")


def _safe_next(path: str | None) -> str | None:
    """Only a path on this site: never another origin. A browser drops tabs
    and line breaks and reads a backslash as a slash, so `/<tab>/evil` and
    `/` + backslash + `evil` would both become `//evil`: none of them."""
    if not path or not path.startswith("/") or path.startswith("//"):
        return None
    if "\\" in path or any(c.isspace() or not c.isprintable() for c in path):
        return None
    return path


def _off() -> None:
    if not google.enabled():
        raise NotFound("Google sign-in isn't set up on this server", code="google_off")


def _back(intent: str, code: str) -> RedirectResponse:
    page = "/account" if intent == "link" else "/login"
    return RedirectResponse(app_link(f"{page}?google={quote(code)}"), status_code=302)


@router.get("/providers", response_model=ProvidersOut)
async def providers() -> ProvidersOut:
    """The ways to sign in here, for the sign-in page."""
    return ProvidersOut(
        password=True,
        google=google.enabled(),
        google_domain=settings.google_allowed_domain or None,
    )


@router.post("/google/link-ticket", response_model=LinkTicketOut)
async def link_ticket(user: CurrentUser, session: SessionDep) -> LinkTicketOut:
    """Connecting Google, signed in: a ticket for `start`, which a browser
    navigation can't otherwise tie to this account. Someone with an
    authenticator confirms with it first (Phase 9.9): a Google sign-in is
    a second factor of its own, so linking must not be easier than that."""
    _off()
    from sqlalchemy import select

    from app.api.errors import Forbidden
    from app.models.second_factor import UserSecondFactor
    from app.security.access import strong_session

    has_factor = await session.scalar(
        select(UserSecondFactor.id).where(
            UserSecondFactor.user_id == user.id, UserSecondFactor.enabled_at.is_not(None)
        )
    )
    if has_factor is not None and not strong_session.get():
        raise Forbidden(
            "Sign in again with your authenticator code before connecting Google",
            code="second_factor_needed",
        )
    return LinkTicketOut(ticket=google_accounts.link_ticket(user))


@router.get("/google/start", dependencies=[SIGNUP])
async def start(
    invite: Annotated[str | None, Query(max_length=200)] = None,
    ticket: Annotated[str | None, Query(max_length=2000)] = None,
    next: Annotated[str | None, Query(max_length=500)] = None,
) -> Response:
    _off()
    link_user_id: str | None = None
    if ticket:
        try:
            link_user_id = google_accounts.ticket_user_id(ticket)
        except GoogleRefused as refused:
            return _back("link", refused.code)
    url, cookie = google.start(
        intent="link" if link_user_id else "login",
        invite=invite,
        link_user_id=link_user_id,
        next_path=_safe_next(next),
    )
    response = RedirectResponse(url, status_code=302)
    response.set_cookie(
        google.COOKIE,
        cookie,
        max_age=int(google.FLOW_TTL.total_seconds()),
        path=google.COOKIE_PATH,
        httponly=True,
        samesite="lax",
        secure=settings.app_base_url.startswith("https://"),
    )
    return response


@router.get("/google/callback", dependencies=[SIGNUP])
async def callback(
    request: Request,
    session: SessionDep,
    ip: ClientIP,
    code: Annotated[str | None, Query(max_length=2000)] = None,
    state: Annotated[str | None, Query(max_length=200)] = None,
    error: Annotated[str | None, Query(max_length=200)] = None,
) -> Response:
    try:
        flow = google.unseal(request.cookies.get(google.COOKIE), state)
    except GoogleRefused as refused:
        log.info("google_refused", code=refused.code, why=str(refused))
        return _back("login", refused.code)
    try:
        if error or not code:
            raise GoogleRefused("cancelled", error or "no code")
        who = google.verify(await google.exchange(code, flow.verifier), flow.nonce)
        if flow.intent == "link" and flow.link_user_id:
            await google_accounts.link(session, who, user_id=flow.link_user_id, ip=ip)
            response: Response = RedirectResponse(
                app_link("/account?google=linked"), status_code=302
            )
        else:
            user = await google_accounts.sign_in(session, who, invite_token=flow.invite, ip=ip)
            raw = await account.issue(session, user, TokenPurpose.google_handoff)
            after = f"&next={quote(flow.next)}" if flow.next else ""
            response = RedirectResponse(
                app_link(f"/auth/google#code={raw}{after}"), status_code=302
            )
    except GoogleRefused as refused:
        log.info("google_refused", code=refused.code, why=str(refused))
        response = _back(flow.intent, refused.code)
    response.delete_cookie(google.COOKIE, path=google.COOKIE_PATH)
    return response


@router.post("/google/exchange", response_model=LoginOut, dependencies=[AUTH])
async def exchange(
    body: GoogleExchange, session: SessionDep, request: Request, ip: ClientIP
) -> LoginOut:
    """The page's one-minute code, for its session tokens. Once.

    A Google sign-in limited to the company's Workspace domain counts as a
    second factor (Phase 8.4): Workspace's own 2-step verification applies
    to it. Otherwise an account with an authenticator finishes with a code."""
    user = await account.take(session, body.code, TokenPurpose.google_handoff)
    strong = bool(settings.google_allowed_domain)
    if not strong and await two_factor.enabled(session, user.id):
        await session.commit()
        return LoginOut(two_factor_challenge=two_factor.challenge(user))
    tokens = await auth_service.issue_tokens(
        session,
        user_id=user.id,
        user_agent=request.headers.get("user-agent"),
        ip=ip,
        strong=strong,
    )
    return LoginOut(
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        expires_in=tokens.expires_in,
    )


@router.delete("/google", response_model=Message)
async def disconnect(user: CurrentUser, session: SessionDep, ip: ClientIP) -> Message:
    """Disconnect Google, when the account has a password to sign in with."""
    await google_accounts.unlink(session, user, ip=ip)
    return Message(message="Google is disconnected.")


__all__ = ["router"]
