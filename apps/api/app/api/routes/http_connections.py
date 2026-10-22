"""HTTP connections (Phase 9.3): a company system an assistant calls. Its
editors manage them; the header values are write-only."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from app.api.deps import ClientIP, EditableAssistantCtx, SessionDep
from app.schemas.common import Message
from app.schemas.http_connection import (
    HttpConnectionCreate,
    HttpConnectionOut,
    HttpConnectionUpdate,
)
from app.services import http_connections as svc

router = APIRouter(tags=["http-connections"])


@router.get("/assistants/{assistant_id}/http-connections", response_model=list[HttpConnectionOut])
async def list_connections(
    ctx: EditableAssistantCtx, session: SessionDep
) -> list[HttpConnectionOut]:
    rows = await svc.list_for_assistant(session, ctx.assistant.id)
    return [HttpConnectionOut.model_validate(r) for r in rows]


@router.post(
    "/assistants/{assistant_id}/http-connections",
    response_model=HttpConnectionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_connection(
    body: HttpConnectionCreate,
    ctx: EditableAssistantCtx,
    session: SessionDep,
    ip: ClientIP,
) -> HttpConnectionOut:
    conn = await svc.create(
        session, assistant=ctx.assistant, body=body, user_id=ctx.membership.user_id, ip=ip
    )
    return HttpConnectionOut.model_validate(conn)


@router.patch(
    "/assistants/{assistant_id}/http-connections/{http_connection_id}",
    response_model=HttpConnectionOut,
)
async def update_connection(
    http_connection_id: uuid.UUID,
    body: HttpConnectionUpdate,
    ctx: EditableAssistantCtx,
    session: SessionDep,
    ip: ClientIP,
) -> HttpConnectionOut:
    conn = await svc.get(session, assistant_id=ctx.assistant.id, connection_id=http_connection_id)
    conn = await svc.update(session, conn, body, user_id=ctx.membership.user_id, ip=ip)
    return HttpConnectionOut.model_validate(conn)


@router.delete(
    "/assistants/{assistant_id}/http-connections/{http_connection_id}",
    response_model=Message,
)
async def delete_connection(
    http_connection_id: uuid.UUID,
    ctx: EditableAssistantCtx,
    session: SessionDep,
    ip: ClientIP,
) -> Message:
    conn = await svc.get(session, assistant_id=ctx.assistant.id, connection_id=http_connection_id)
    await svc.delete(session, conn, user_id=ctx.membership.user_id, ip=ip)
    return Message(message="deleted")


__all__ = ["router"]
