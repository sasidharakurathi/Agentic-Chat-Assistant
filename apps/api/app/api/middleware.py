"""Request-scoped context: a request id + access-log line, both structured."""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.logging import get_logger, request_id_ctx

log = get_logger("api.access")

_REQUEST_ID_HEADER = "x-request-id"


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
        # Health checks are noisy; log them at debug only.
        emit = log.debug if request.url.path in ("/healthz", "/readyz") else log.info
        emit(
            "request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            elapsed_ms=elapsed_ms,
        )
        return response


__all__ = ["RequestContextMiddleware"]
