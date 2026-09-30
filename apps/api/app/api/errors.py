"""Application error hierarchy + FastAPI exception handlers.

All handled errors render as a small JSON envelope:

    {"error": {"code": "forbidden", "message": "...", "details": {...}}, "request_id": "..."}
"""

from __future__ import annotations

import math
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.logging import get_logger, request_id_ctx

log = get_logger(__name__)


class AppError(Exception):
    status_code: int = status.HTTP_400_BAD_REQUEST
    code: str = "bad_request"

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
        code: str | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message or self.code)
        self.message = message or self.__class__.__doc__ or self.code
        self.details = details or {}
        #: Extra response headers (e.g. `Retry-After` on a 429).
        self.headers: dict[str, str] = {}
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code


class BadRequest(AppError):
    status_code = status.HTTP_400_BAD_REQUEST
    code = "bad_request"


class Unauthorized(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "unauthorized"


class Forbidden(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "forbidden"


class NotFound(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"


class Conflict(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "conflict"


class RateLimited(AppError):
    """Too many requests (task 5.8): says when to try again, in words and
    in `Retry-After`."""

    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "rate_limited"

    def __init__(self, message: str, retry_after_s: float) -> None:
        seconds = max(1, math.ceil(retry_after_s))
        super().__init__(
            f"{message} Try again in {seconds} second{'s' if seconds != 1 else ''}.",
            details={"retry_after_s": seconds},
        )
        self.headers = {"Retry-After": str(seconds)}


def _envelope(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"error": {"code": code, "message": message}}
    if details:
        body["error"]["details"] = details
    rid = request_id_ctx.get()
    if rid:
        body["request_id"] = rid
    return body


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope(exc.code, exc.message, exc.details),
            headers=exc.headers or None,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # `input` is dropped from every error. For a model-level validator, or
        # a missing field, pydantic sets it to the *whole request body* — so
        # a 422 used to echo back whatever password or connection string came
        # with the request, into proxy logs and error trackers. The message
        # and location still say what is wrong; they never repeat a value.
        #
        # And encoded with `jsonable_encoder`: a model-level validator that
        # raises ValueError puts the exception *object* in `ctx`, which plain
        # JSON cannot serialise — so every such validation failure crashed
        # this handler and never produced its 422 (the client got no
        # response at all).
        errors = jsonable_encoder(
            [{k: v for k, v in e.items() if k != "input"} for e in exc.errors()],
            custom_encoder={BaseException: str},
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=_envelope("validation_error", "Request validation failed", {"errors": errors}),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope("http_error", str(exc.detail)),
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_exception", error=str(exc))
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_envelope("internal_error", "An unexpected error occurred"),
        )


__all__ = [
    "AppError",
    "BadRequest",
    "Conflict",
    "Forbidden",
    "NotFound",
    "RateLimited",
    "Unauthorized",
    "register_exception_handlers",
]
