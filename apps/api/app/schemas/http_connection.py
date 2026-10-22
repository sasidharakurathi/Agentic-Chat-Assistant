"""HTTP connections (Phase 9.3): what a builder sends and sees."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import ORMModel

_NAME = r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,79}$"
#: Headers the platform owns: the request's framing, and who is asking.
RESERVED_HEADERS = frozenset(
    {
        "host",
        "content-length",
        "transfer-encoding",
        "connection",
        "upgrade",
        "content-type",
        "x-asker-email",
        "x-asker-name",
        "x-asker-id",
    }
)


def _headers(v: dict[str, str] | None) -> dict[str, str] | None:
    if v is None:
        return v
    if len(v) > 20:  # noqa: PLR2004
        raise ValueError("at most 20 headers")
    out = {}
    for k, val in v.items():
        key = k.strip()
        if not key or not all(c.isalnum() or c in "-_" for c in key):
            raise ValueError(f"{k!r} is not a header name")
        if key.lower() in RESERVED_HEADERS:
            raise ValueError(f"{key} is set by the platform")
        if "\r" in val or "\n" in val or len(val) > 4000:  # noqa: PLR2004
            raise ValueError(f"the value of {key} isn't valid")
        out[key] = val
    return out


class HttpConnectionCreate(BaseModel):
    name: str = Field(pattern=_NAME)
    base_url: str = Field(min_length=8, max_length=2048)
    #: Write-only: sealed, never returned. An API key, a bearer token.
    headers: dict[str, str] = Field(default_factory=dict)
    send_asker: bool = True

    _h = field_validator("headers")(_headers)


class HttpConnectionUpdate(BaseModel):
    """Only what is sent changes; `headers` replaces them all."""

    name: str | None = Field(default=None, pattern=_NAME)
    base_url: str | None = Field(default=None, min_length=8, max_length=2048)
    headers: dict[str, str] | None = None
    send_asker: bool | None = None

    _h = field_validator("headers")(_headers)


class HttpConnectionOut(ORMModel):
    id: uuid.UUID
    name: str
    base_url: str
    #: The names only: the values are sealed.
    header_names: list[str]
    send_asker: bool
    created_at: datetime


__all__ = [
    "RESERVED_HEADERS",
    "HttpConnectionCreate",
    "HttpConnectionOut",
    "HttpConnectionUpdate",
]
