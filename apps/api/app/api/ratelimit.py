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

from collections.abc import Awaitable, Callable
from functools import lru_cache

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
    "refresh": "This session is being refreshed too often.",
    "refresh_ip": "Too many session refreshes from this address.",
    "chat_user": "You're sending messages too quickly.",
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
            await enforce("ip", client_ip(request), settings.rate_limit_ip)
        except RateLimited as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content=_envelope(exc.code, exc.message, exc.details),
                headers=exc.headers,
            )
        return await call_next(request)


__all__ = ["RateLimitMiddleware", "client_ip", "enforce"]
