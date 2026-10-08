"""Request-scoped context: a request id + access-log line, both structured."""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.api import ratelimit
from app.logging import get_logger, request_id_ctx
from app.observability import metrics

log = get_logger("api.access")

_REQUEST_ID_HEADER = "x-request-id"
#: On every response (task 6.3): an API answer is never something to frame,
#: to sniff a type for, or to leak an address from. `setdefault`, so a route
#: that needs something different can say so.
#: Probes and the scrape itself are not traffic.
_UNMEASURED = frozenset({"/healthz", "/readyz", "/metrics"})
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "frame-ancestors 'none'",
}


def _full_template(path: str, template: str) -> str:
    """The route's template with the prefix it was included under.

    A route inside an included router knows its own path (`/assistants/{id}`)
    and not the router's prefix (`/api/v1`). The template has as many
    segments as the part of the address it matched, so whatever comes before
    that in the address is the prefix.
    """
    parts, wanted = path.strip("/").split("/"), template.strip("/").split("/")
    prefix = parts[: max(0, len(parts) - len(wanted))]
    return "/" + "/".join([*prefix, *wanted]) if prefix else template


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        rid = request.headers.get(_REQUEST_ID_HEADER) or uuid.uuid4().hex
        token = request_id_ctx.set(rid)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
            log.exception(
                "request_failed",
                method=request.method,
                path=request.url.path,
                elapsed_ms=elapsed_ms,
            )
            raise
        finally:
            request_id_ctx.reset(token)

        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        response.headers[_REQUEST_ID_HEADER] = rid
        for name, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        # Health checks are noisy; log them at debug only.
        emit = log.debug if request.url.path in ("/healthz", "/readyz") else log.info
        # The route's template when there is one (task 6.3): an invite link
        # is `/invites/{token}`, and the raw path put the token, which is
        # the whole credential, into every log line and trace for it.
        route = request.scope.get("route")
        template = getattr(route, "path", None)
        if template is not None:
            template = _full_template(request.url.path, template)
        if request.url.path not in _UNMEASURED:
            # By route template, so an id never becomes a label; everything
            # that matched no route shares one series (task 6.4).
            label = template or "unmatched"
            metrics.HTTP_REQUESTS.inc(
                request.method, label, metrics.status_class(response.status_code)
            )
            metrics.HTTP_DURATION.observe(elapsed_ms / 1000, request.method, label)
        emit(
            "request",
            method=request.method,
            path=template or request.url.path,
            status=response.status_code,
            elapsed_ms=elapsed_ms,
            # Who, and from where (Phase 7a.8): the line alone used to say
            # nothing about either, so the logs kept could not answer an
            # incident's first question. The address is the one the rate
            # limiter believes (TRUSTED_PROXY_HOPS).
            user_id=getattr(request.state, "user_id", None),
            client_ip=ratelimit.client_ip(request),
        )
        return response


__all__ = ["RequestContextMiddleware"]
