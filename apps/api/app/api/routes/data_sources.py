from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, File, Form, UploadFile, status

from app.api.deps import (
    AssistantCtx,
    ClientIP,
    EditableAssistantCtx,
    PageQuery,
    SessionDep,
    per_user_limit,
)
from app.models.rag import DataSourceStatus
from app.rag import progress as ingest_progress
from app.schemas.common import Message, Page
from app.schemas.data_source import (
    ContentUrl,
    CreateUrlSource,
    DataSourceCreate,
    DataSourceSummary,
)
from app.services import data_sources as svc

router = APIRouter(tags=["data-sources"])

#: Reaches other systems or starts a job: limited per caller (task 5.8).
HEAVY = per_user_limit("heavy", "rate_limit_heavy")


@router.get("/assistants/{assistant_id}/data-sources", response_model=Page[DataSourceSummary])
async def list_data_sources(
    ctx: AssistantCtx, session: SessionDep, page: PageQuery
) -> Page[DataSourceSummary]:
    rows = await svc.page_for_assistant(
        session, ctx.assistant.id, limit=page.limit, cursor=page.cursor
    )
    # One grouped query for every row's counts, not two per row.
    counts = await svc.counts_for(session, ctx.assistant.id)
    # Live progress for the ones still being indexed (plan §5.1 step 7).
    live = await ingest_progress.read(
        [r.id for r in rows.items if r.status == DataSourceStatus.processing]
    )
    return Page(
        items=[svc.to_summary(r, counts.get(r.id), live.get(r.id)) for r in rows.items],
        next_cursor=rows.next_cursor,
    )


@router.post(
    "/assistants/{assistant_id}/data-sources",
    dependencies=[HEAVY],
    response_model=DataSourceSummary,
    status_code=status.HTTP_201_CREATED,
)
async def create_data_source(
    body: DataSourceCreate,
    ctx: EditableAssistantCtx,
    session: SessionDep,
    ip: ClientIP,
) -> DataSourceSummary:
    if isinstance(body, CreateUrlSource):
        source = await svc.create_url(
            session,
            assistant=ctx.assistant,
            name=body.name,
            url=body.url,
            user_id=ctx.membership.user_id,
            ip=ip,
        )
    else:
        source = await svc.create_text(
            session,
            assistant=ctx.assistant,
            name=body.name,
            text=body.text,
            user_id=ctx.membership.user_id,
            ip=ip,
        )
    return DataSourceSummary.model_validate(source)


@router.post(
    "/assistants/{assistant_id}/data-sources/upload",
    dependencies=[HEAVY],
    response_model=DataSourceSummary,
    status_code=status.HTTP_201_CREATED,
)
async def upload_data_source(
    ctx: EditableAssistantCtx,
    session: SessionDep,
    ip: ClientIP,
    file: Annotated[UploadFile, File()],
    name: Annotated[str | None, Form()] = None,
) -> DataSourceSummary:
    content = await file.read()
    source = await svc.create_upload(
        session,
        assistant=ctx.assistant,
        filename=name or file.filename or "upload",
        content=content,
        content_type=file.content_type or "application/octet-stream",
        user_id=ctx.membership.user_id,
        ip=ip,
    )
    return DataSourceSummary.model_validate(source)


@router.get(
    "/assistants/{assistant_id}/data-sources/{data_source_id}",
    response_model=DataSourceSummary,
)
async def get_data_source(
    ctx: AssistantCtx, session: SessionDep, data_source_id: uuid.UUID
) -> DataSourceSummary:
    source = await svc.get(session, assistant_id=ctx.assistant.id, data_source_id=data_source_id)
    counts = await svc.counts_for(session, ctx.assistant.id)
    return svc.to_summary(source, counts.get(source.id))


@router.get(
    "/assistants/{assistant_id}/data-sources/{data_source_id}/content-url",
    response_model=ContentUrl,
)
async def data_source_content_url(
    ctx: AssistantCtx, session: SessionDep, data_source_id: uuid.UUID
) -> ContentUrl:
    """A short-lived link to the original file, for citation deep links.

    Tenant scoping is the important part: ``svc.get`` filters by
    ``ctx.assistant.id``, and ``AssistantCtx`` has already 404'd if the caller
    isn't a member of the assistant's org — so there's no path from "I know a
    data source id" to "I can mint a URL for another tenant's object".
    """
    source = await svc.get(session, assistant_id=ctx.assistant.id, data_source_id=data_source_id)
    return await svc.content_url(source)


@router.post(
    "/assistants/{assistant_id}/data-sources/{data_source_id}:reindex",
    dependencies=[HEAVY],
    response_model=DataSourceSummary,
)
async def reindex_data_source(
    ctx: EditableAssistantCtx, session: SessionDep, ip: ClientIP, data_source_id: uuid.UUID
) -> DataSourceSummary:
    source = await svc.get(session, assistant_id=ctx.assistant.id, data_source_id=data_source_id)
    source = await svc.reindex(session, source, user_id=ctx.membership.user_id, ip=ip)
    return DataSourceSummary.model_validate(source)


@router.delete(
    "/assistants/{assistant_id}/data-sources/{data_source_id}",
    response_model=Message,
)
async def delete_data_source(
    ctx: EditableAssistantCtx, session: SessionDep, ip: ClientIP, data_source_id: uuid.UUID
) -> Message:
    source = await svc.get(session, assistant_id=ctx.assistant.id, data_source_id=data_source_id)
    await svc.delete(session, source, user_id=ctx.membership.user_id, ip=ip)
    return Message(message="deleted")


__all__ = ["router"]
