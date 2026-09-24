from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")


#: Pydantic leaves every field that has a default out of `required`, response
#: schemas included, so fields the server *always* sends came out optional in
#: OpenAPI and `field?: T` in the generated TypeScript. Serialization-mode
#: schemas (responses) now mark them required; request schemas are unchanged,
#: because a default there really does mean "may be omitted". Where the two
#: differ, FastAPI publishes a `-Input` and an `-Output` schema.
_RESPONSE_DEFAULTS = ConfigDict(json_schema_serialization_defaults_required=True)


class ApiModel(BaseModel):
    model_config = _RESPONSE_DEFAULTS


class ORMModel(BaseModel):
    model_config = ConfigDict(
        from_attributes=True, json_schema_serialization_defaults_required=True
    )


class Page(ApiModel, Generic[T]):
    """One page of a list endpoint (plan §8: every list is paginated).

    `next_cursor` is opaque; pass it back as `?cursor=` for the next page.
    `null` means this was the last page."""

    items: list[T]
    next_cursor: str | None = None


class Message(ApiModel):
    message: str


class HealthResponse(ApiModel):
    status: str
    version: str


class ReadyResponse(ApiModel):
    status: str
    checks: dict[str, str]


__all__ = ["ApiModel", "HealthResponse", "Message", "ORMModel", "Page", "ReadyResponse"]
