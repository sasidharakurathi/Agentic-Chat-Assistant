from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from app.api.deps import AssistantCtx, ClientIP, EditableAssistantCtx, PageQuery, SessionDep
from app.schemas.common import Message, Page
from app.schemas.db_connection import (
    DbConnectionCreate,
    DbConnectionSummary,
    DbConnectionUpdate,
    DbSchemaOut,
    DbTestResult,
)
from app.services import db_connections as svc

router = APIRouter(tags=["db-connections"])


@router.get("/assistants/{assistant_id}/db-connections", response_model=Page[DbConnectionSummary])
async def list_connections(
    ctx: AssistantCtx, session: SessionDep, page: PageQuery
) -> Page[DbConnectionSummary]:
    rows = await svc.page_for_assistant(
        session, ctx.assistant.id, limit=page.limit, cursor=page.cursor
    )
    return Page(items=[svc.to_summary(r) for r in rows.items], next_cursor=rows.next_cursor)


@router.post(
    "/assistants/{assistant_id}/db-connections",
    response_model=DbConnectionSummary,
    status_code=status.HTTP_201_CREATED,
)
async def create_connection(
    body: DbConnectionCreate, ctx: EditableAssistantCtx, session: SessionDep, ip: ClientIP
) -> DbConnectionSummary:
    conn = await svc.create(
        session, assistant=ctx.assistant, body=body, user_id=ctx.membership.user_id, ip=ip
    )
    return svc.to_summary(conn)


@router.get(
    "/assistants/{assistant_id}/db-connections/{connection_id}",
    response_model=DbConnectionSummary,
)
async def get_connection(
    ctx: AssistantCtx, session: SessionDep, connection_id: uuid.UUID
) -> DbConnectionSummary:
    conn = await svc.get(session, assistant_id=ctx.assistant.id, connection_id=connection_id)
    return svc.to_summary(conn)


@router.patch(
    "/assistants/{assistant_id}/db-connections/{connection_id}",
    response_model=DbConnectionSummary,
)
async def update_connection(
    body: DbConnectionUpdate,
    ctx: EditableAssistantCtx,
    session: SessionDep,
    ip: ClientIP,
    connection_id: uuid.UUID,
) -> DbConnectionSummary:
    conn = await svc.get(session, assistant_id=ctx.assistant.id, connection_id=connection_id)
    conn = await svc.update(session, conn, body, user_id=ctx.membership.user_id, ip=ip)
    return svc.to_summary(conn)


@router.delete("/assistants/{assistant_id}/db-connections/{connection_id}", response_model=Message)
async def delete_connection(
    ctx: EditableAssistantCtx, session: SessionDep, ip: ClientIP, connection_id: uuid.UUID
) -> Message:
    conn = await svc.get(session, assistant_id=ctx.assistant.id, connection_id=connection_id)
    await svc.delete(session, conn, user_id=ctx.membership.user_id, ip=ip)
    return Message(message="deleted")


@router.post(
    "/assistants/{assistant_id}/db-connections/{connection_id}:test",
    response_model=DbTestResult,
)
async def test_connection(
    ctx: EditableAssistantCtx, session: SessionDep, connection_id: uuid.UUID
) -> DbTestResult:
    """Open a connection and round-trip a trivial query.

    Returns `ok: false` with the driver's message rather than raising: a
    wrong password is a normal thing for someone setting this up to do, and
    a 500 would make the form look broken instead of the credential.
    """
    conn = await svc.get(session, assistant_id=ctx.assistant.id, connection_id=connection_id)
    return await svc.test_connection(session, conn)


@router.post(
    "/assistants/{assistant_id}/db-connections/{connection_id}:refresh-schema",
    response_model=DbSchemaOut,
)
async def refresh_schema(
    ctx: EditableAssistantCtx, session: SessionDep, connection_id: uuid.UUID
) -> DbSchemaOut:
    conn = await svc.get(session, assistant_id=ctx.assistant.id, connection_id=connection_id)
    return await svc.refresh_schema(session, conn)


@router.get(
    "/assistants/{assistant_id}/db-connections/{connection_id}/schema",
    response_model=DbSchemaOut,
)
async def get_schema(
    ctx: AssistantCtx, session: SessionDep, connection_id: uuid.UUID
) -> DbSchemaOut:
    conn = await svc.get(session, assistant_id=ctx.assistant.id, connection_id=connection_id)
    return await svc.cached_schema(session, conn)


__all__ = ["router"]
