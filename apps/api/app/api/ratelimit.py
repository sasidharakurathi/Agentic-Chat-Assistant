"""Where the rate limits apply (task 5.8). The buckets are in
`security/ratelimit.py`; the limits are settings (`RATE_LIMIT_*`).

- **Every API request, per client IP** (`RateLimitMiddleware`), before any
  work is done for it.
- **Every authenticated request, per user** (`get_current_user`), so an API
  token spread across many machines is still one caller.
- **Where a request costs or can be guessed at**, per IP, user or org:
  registering, logging in (also per account), refreshing a token, sending a
  chat message (per user and per org), the AI helpers, and work that reaches
  other systems or starts jobs.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Awaitable, Callable
from functools import cache, lru_cache

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.api.errors import RateLimited, _envelope
from app.config import settings
from app.security import ratelimit

#: What the 429 says, per bucket; the time to wait is added to it.
_MESSAGES = {
    "ip": "Too many requests from this address.",
    "user": "Too many requests.",
    "auth": "Too many sign-in attempts from this address.",
    "login": "Too many sign-in attempts for this account.",
    # Phase 8.4: creating accounts, and starting Google sign-in.
    "signup": "Too many sign-ups from this address.",
    "refresh": "This session is being refreshed too often.",
    "refresh_ip": "Too many session refreshes from this address.",
    "chat_user": "You're sending messages too quickly.",
    "chat_employee": "You're sending messages too quickly.",
    "chat_org": "This organisation is sending messages too quickly.",
    "assist": "Too many requests to the AI helpers.",
    "heavy": "Too many of these requests.",
}

#: Paths no limit applies to: probes must always answer.
_EXEMPT = ("/healthz", "/readyz", "/metrics")


@lru_cache
def _limit(spec: str) -> ratelimit.Limit:
    return ratelimit.Limit.parse(spec)


def client_ip(request: Request) -> str:
    """The client's address. X-Forwarded-For is only believed for as many
    hops as there are trusted proxies (`TRUSTED_PROXY_HOPS`): anyone can send
    the header, and believing it would let a caller pick their own IP, for
    the rate limits and the audit log alike."""
    peer = request.client.host if request.client else "unknown"
    hops = settings.trusted_proxy_hops
    if hops <= 0:
        return peer
    chain = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
    # Each trusted proxy appends the address it saw; the client is the one
    # the outermost trusted proxy saw.
    return chain[-hops] if len(chain) >= hops else (chain[0] if chain else peer)


@cache
def _internal_nets(raw: str) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    nets = []
    for part in raw.split(","):
        if part.strip():
            nets.append(ipaddress.ip_network(part.strip(), strict=False))
    return tuple(nets)


def is_internal(ip: str | None) -> bool:
    """From TRUSTED_INTERNAL_NETS (Phase 8.4): an office or VPN range, where
    many people arrive from one address."""
    if not ip or not settings.trusted_internal_nets:
        return False
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(address in net for net in _internal_nets(settings.trusted_internal_nets))


def per_address(ip: str | None, spec: str, internal_spec: str) -> str:
    """The per-address limit for this address."""
    return internal_spec if is_internal(ip) else spec


async def enforce(bucket: str, subject: str, spec: str, *, cost: int = 1) -> None:
    """Take from `bucket` for `subject`, or raise a 429."""
    if not settings.rate_limit_enabled:
        return
    decision = await ratelimit.take(bucket, subject, _limit(spec), cost)
    if not decision.allowed:
        raise RateLimited(_MESSAGES.get(bucket, "Too many requests."), decision.retry_after_s)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Every API request, per client IP."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if (
            not settings.rate_limit_enabled
            or request.method == "OPTIONS"  # CORS preflights
            or request.url.path in _EXEMPT
        ):
            return await call_next(request)
        try:
            ip = client_ip(request)
            await enforce(
                "ip", ip, per_address(ip, settings.rate_limit_ip, settings.rate_limit_ip_internal)
            )
        except RateLimited as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content=_envelope(exc.code, exc.message, exc.details),
                headers=exc.headers,
            )
        return await call_next(request)


__all__ = ["RateLimitMiddleware", "client_ip", "enforce", "is_internal", "per_address"]
