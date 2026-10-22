"""Google sign-in, the protocol (Phase 8.3): OpenID Connect's
authorization-code flow, with PKCE, a nonce, and a state bound to the
browser.

What is checked, and where:

- **state, nonce, PKCE**: kept in a short-lived, HttpOnly cookie signed
  with the server's JWT secret, on the Google routes' path only. The
  callback must bring back the same state (no one else's sign-in can be
  finished in this browser), and Google's token must carry the same nonce.
  The PKCE verifier proves the code is redeemed by whoever asked for it.
- **Google's ID token**: its signature against Google's published keys,
  its issuer, that it was made for this app (audience), that it hasn't
  expired, the nonce, that Google verified the address, and, when
  `GOOGLE_ALLOWED_DOMAIN` is set, that the account belongs to that Google
  Workspace domain by the `hd` claim, never by the address's ending: an
  address at the company's domain can belong to a personal Google account.

Who the token is, and what that lets them do, is `services/google_accounts`.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import cache
from typing import Any, Literal
from urllib.parse import urlencode

import httpx
import jwt

from app.config import settings

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
ISSUERS = ("https://accounts.google.com", "accounts.google.com")

#: The cookie that carries a sign-in's state between start and callback.
COOKIE = "as_google"
COOKIE_PATH = "/api/v1/auth/google"
#: Long enough to pick an account and type a password; no longer.
FLOW_TTL = timedelta(minutes=10)

Intent = Literal["login", "link"]


class GoogleRefused(Exception):
    """A sign-in that doesn't go through. `code` reaches the page, which
    words it; `message` is for the log."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


@dataclass(frozen=True)
class Flow:
    """One sign-in under way, as the cookie keeps it."""

    state: str
    nonce: str
    verifier: str
    intent: Intent
    #: An invite link's token, when the person came from one.
    invite: str | None = None
    #: Linking: the account that asked, from a ticket it was given signed in.
    link_user_id: str | None = None
    #: Where the page goes after signing in.
    next: str | None = None


@dataclass(frozen=True)
class GoogleIdentity:
    subject: str
    email: str
    email_verified: bool
    domain: str | None
    name: str


def enabled() -> bool:
    return bool(settings.google_client_id and settings.google_client_secret)


def redirect_uri() -> str:
    return settings.google_redirect_uri or (
        f"{settings.app_base_url.rstrip('/')}/api/v1/auth/google/callback"
    )


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def start(
    *,
    intent: Intent,
    invite: str | None = None,
    link_user_id: str | None = None,
    next_path: str | None = None,
) -> tuple[str, str]:
    """The address to send the browser to, and the cookie to set first."""
    flow = Flow(
        state=secrets.token_urlsafe(24),
        nonce=secrets.token_urlsafe(24),
        verifier=secrets.token_urlsafe(48),
        intent=intent,
        invite=invite,
        link_user_id=link_user_id,
        next=next_path,
    )
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": "openid email profile",
        "state": flow.state,
        "nonce": flow.nonce,
        "code_challenge": _challenge(flow.verifier),
        "code_challenge_method": "S256",
        "prompt": "select_account",
    }
    if settings.google_allowed_domain:
        # A hint for Google's account picker only: the `hd` claim in the
        # token is what is checked.
        params["hd"] = settings.google_allowed_domain
    return f"{AUTH_URL}?{urlencode(params)}", seal(flow)


def seal(flow: Flow) -> str:
    payload: dict[str, Any] = {
        "typ": "google_flow",
        "exp": datetime.now(UTC) + FLOW_TTL,
        **{k: v for k, v in flow.__dict__.items() if v is not None},
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def unseal(cookie: str | None, state: str | None) -> Flow:
    """The flow this browser started, if `state` is the one it was given."""
    if not cookie or not state:
        raise GoogleRefused("state", "no sign-in under way in this browser")
    try:
        data = jwt.decode(cookie, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise GoogleRefused("state", "the sign-in expired or was tampered with") from exc
    if data.get("typ") != "google_flow" or not secrets.compare_digest(
        str(data.get("state", "")), state
    ):
        raise GoogleRefused("state", "the state doesn't match this browser's sign-in")
    return Flow(
        state=data["state"],
        nonce=data["nonce"],
        verifier=data["verifier"],
        intent=data["intent"],
        invite=data.get("invite"),
        link_user_id=data.get("link_user_id"),
        next=data.get("next"),
    )


async def exchange(code: str, verifier: str) -> str:
    """Google's ID token, for the code it gave the browser."""
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": redirect_uri(),
                "grant_type": "authorization_code",
                "code_verifier": verifier,
            },
        )
    if not r.is_success:
        raise GoogleRefused("exchange", f"Google refused the code ({r.status_code})")
    token = r.json().get("id_token")
    if not isinstance(token, str):
        raise GoogleRefused("exchange", "Google sent no ID token")
    return token


@cache
def _keys() -> jwt.PyJWKClient:
    # Cached, keys included: Google rotates them every few weeks, and an
    # unknown key id makes the client fetch them again.
    return jwt.PyJWKClient(JWKS_URL, cache_keys=True)


def verify(id_token: str, nonce: str) -> GoogleIdentity:
    """Google's ID token, checked; who it says the person is."""
    try:
        key = _keys().get_signing_key_from_jwt(id_token).key
        claims = jwt.decode(
            id_token,
            key,
            algorithms=["RS256"],
            audience=settings.google_client_id,
            issuer=ISSUERS,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise GoogleRefused(
            "token", f"the ID token didn't check out: {type(exc).__name__}"
        ) from exc
    if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        raise GoogleRefused("token", "the nonce doesn't match")
    if claims.get("email_verified") is not True or not claims.get("email"):
        raise GoogleRefused("unverified", "Google hasn't verified this address")
    domain = claims.get("hd")
    wanted = settings.google_allowed_domain.strip().lower()
    if wanted and (not isinstance(domain, str) or domain.lower() != wanted):
        raise GoogleRefused("domain", "not an account of the allowed Google Workspace domain")
    return GoogleIdentity(
        subject=str(claims["sub"]),
        email=str(claims["email"]).strip().lower(),
        email_verified=True,
        domain=domain.lower() if isinstance(domain, str) else None,
        name=str(claims.get("name") or ""),
    )


__all__ = [
    "COOKIE",
    "COOKIE_PATH",
    "Flow",
    "GoogleIdentity",
    "GoogleRefused",
    "enabled",
    "exchange",
    "redirect_uri",
    "seal",
    "start",
    "unseal",
    "verify",
]
