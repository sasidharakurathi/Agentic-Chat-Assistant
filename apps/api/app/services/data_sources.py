"""Data source CRUD + enqueueing ingestion (parse -> chunk -> embed -> index,
``app/rag/ingest.py``, run by the Arq worker in ``app/worker.py``). Creating
or reindexing a source enqueues a job here; nothing in this module touches
``documents``/``chunks`` directly.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import BadRequest, NotFound
from app.db.pagination import PageResult, keyset_page
from app.models.assistant import Assistant
from app.models.rag import Chunk, DataSource, DataSourceStatus, DataSourceType, Document
from app.queue import enqueue_ingest
from app.schemas.data_source import ContentUrl, DataSourceSummary, IngestProgress
from app.services import audit
from app.storage import delete_object, presigned_get_url, put_object
from app.storage.file_types import (
    UnsupportedUpload,
    classify_upload,
    content_disposition,
    serve_kind,
)

_MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB


#: What a source says when its ingestion could not be queued.
NOT_QUEUED = (
    "Indexing could not be started: the job queue is unavailable. "
    "Use Reindex to try again once it is back."
)


async def _queue(session: AsyncSession, source: DataSource) -> None:
    """Queue ingestion, or say why it isn't happening.

    `enqueue_ingest` used to swallow a queue outage with a log line, leaving
    the source at "pending" for good: no worker would ever see it, and the
    sources page polled a spinner forever."""
    if not await enqueue_ingest(source.id):
        source.status = DataSourceStatus.error
        source.error = NOT_QUEUED
        await session.commit()


def _object_key(assistant_id: uuid.UUID, data_source_id: uuid.UUID, filename: str) -> str:
    safe_name = filename.replace("/", "_").replace("\\", "_") or "upload"
    return f"data-sources/{assistant_id}/{data_source_id}/{safe_name}"


async def create_url(
    session: AsyncSession,
    *,
    assistant: Assistant,
    name: str,
    url: str,
    user_id: uuid.UUID,
    ip: str | None = None,
) -> DataSource:
    source = DataSource(
        assistant_id=assistant.id,
        org_id=assistant.org_id,
        type=DataSourceType.url,
        name=name.strip(),
        uri=url.strip(),
        status=DataSourceStatus.pending,
    )
    session.add(source)
    await session.flush()
    await audit.record(
        session,
        action="data_source.create",
        org_id=assistant.org_id,
        actor_user_id=user_id,
        target_type="data_source",
        target_id=source.id,
        meta={"type": "url"},
        ip=ip,
    )
    await session.commit()
    await _queue(session, source)
    return source


async def create_text(
    session: AsyncSession,
    *,
    assistant: Assistant,
    name: str,
    text: str,
    user_id: uuid.UUID,
    ip: str | None = None,
) -> DataSource:
    source = DataSource(
        assistant_id=assistant.id,
        org_id=assistant.org_id,
        type=DataSourceType.text,
        name=name.strip(),
        bytes=len(text.encode("utf-8")),
        status=DataSourceStatus.pending,
        # Ingestion (2.5) reads the pasted text from here to create the one
        # Document this source produces — there's nowhere else to put it yet.
        config={"text": text},
    )
    session.add(source)
    await session.flush()
    await audit.record(
        session,
        action="data_source.create",
        org_id=assistant.org_id,
        actor_user_id=user_id,
        target_type="data_source",
        target_id=source.id,
        meta={"type": "text"},
        ip=ip,
    )
    await session.commit()
    await _queue(session, source)
    return source


async def create_upload(
    session: AsyncSession,
    *,
    assistant: Assistant,
    filename: str,
    content: bytes,
    content_type: str,
    user_id: uuid.UUID,
    ip: str | None = None,
) -> DataSource:
    if len(content) > _MAX_UPLOAD_BYTES:
        raise BadRequest(
            f"File exceeds the {_MAX_UPLOAD_BYTES // (1024 * 1024)} MB upload limit",
            code="file_too_large",
        )
    # The server decides what the file is; the browser's type is only a
    # tie-break between text formats (storage/file_types.py).
    try:
        kind = classify_upload(filename, content, content_type)
    except UnsupportedUpload as exc:
        raise BadRequest(str(exc), code="unsupported_file_type") from exc

    source = DataSource(
        assistant_id=assistant.id,
        org_id=assistant.org_id,
        type=DataSourceType.file,
        name=filename,
        bytes=len(content),
        checksum=hashlib.sha256(content).hexdigest(),
        status=DataSourceStatus.pending,
        config={"content_type": kind.mime},
    )
    session.add(source)
    await session.flush()

    key = _object_key(assistant.id, source.id, filename)
    await put_object(key, content, kind.mime)
    source.object_key = key

    await audit.record(
        session,
        action="data_source.create",
        org_id=assistant.org_id,
        actor_user_id=user_id,
        target_type="data_source",
        target_id=source.id,
        meta={"type": "file", "bytes": len(content)},
        ip=ip,
    )
    await session.commit()
    await _queue(session, source)
    return source


async def list_for_assistant(session: AsyncSession, assistant_id: uuid.UUID) -> list[DataSource]:
    """Every row, unpaged, for the model's `kb_list_sources` tool, which needs the
    whole catalogue. The API lists through `page_for_assistant`."""
    rows = await session.scalars(
        select(DataSource)
        .where(DataSource.assistant_id == assistant_id)
        .order_by(DataSource.created_at.desc(), DataSource.id.desc())
    )
    return list(rows)


async def page_for_assistant(
    session: AsyncSession, assistant_id: uuid.UUID, *, limit: int = 50, cursor: str | None = None
) -> PageResult[DataSource]:
    return await keyset_page(
        session,
        select(DataSource).where(DataSource.assistant_id == assistant_id),
        [DataSource.created_at, DataSource.id],
        limit=limit,
        cursor=cursor,
    )


async def counts_for(
    session: AsyncSession, assistant_id: uuid.UUID
) -> dict[uuid.UUID, tuple[int, int]]:
    """``{data_source_id: (documents, chunks)}`` for one assistant.

    One grouped query for the whole listing rather than two per row — a
    sources page with twenty files would otherwise fire forty extra queries
    just to render its counts. ``count(distinct Document.id)`` because the
    outer join to chunks multiplies document rows, and ``count(Chunk.id)``
    (not ``count(*)``) so a document with no chunks counts zero rather than
    one.
    """
    rows = (
        await session.execute(
            select(
                Document.data_source_id,
                func.count(func.distinct(Document.id)),
                func.count(Chunk.id),
            )
            .outerjoin(Chunk, Chunk.document_id == Document.id)
            .where(Document.assistant_id == assistant_id)
            .group_by(Document.data_source_id)
        )
    ).all()
    return {row[0]: (int(row[1]), int(row[2])) for row in rows}


def to_summary(
    source: DataSource,
    counts: tuple[int, int] | None = None,
    live: dict[str, Any] | None = None,
) -> DataSourceSummary:
    docs, chunks = counts or (0, 0)
    update: dict[str, Any] = {"document_count": docs, "chunk_count": chunks}
    if live is not None and source.status == DataSourceStatus.processing:
        update["progress"] = IngestProgress.model_validate(live)
    return DataSourceSummary.model_validate(source).model_copy(update=update)


async def get(
    session: AsyncSession, *, assistant_id: uuid.UUID, data_source_id: uuid.UUID
) -> DataSource:
    source = await session.scalar(
        select(DataSource).where(
            DataSource.id == data_source_id, DataSource.assistant_id == assistant_id
        )
    )
    if source is None:
        raise NotFound("Data source not found")
    return source


async def reindex(
    session: AsyncSession, source: DataSource, *, user_id: uuid.UUID, ip: str | None = None
) -> DataSource:
    source.status = DataSourceStatus.pending
    source.error = None
    await audit.record(
        session,
        action="data_source.reindex",
        org_id=source.org_id,
        actor_user_id=user_id,
        target_type="data_source",
        target_id=source.id,
        ip=ip,
    )
    await session.commit()
    await _queue(session, source)
    return source


async def delete(
    session: AsyncSession, source: DataSource, *, user_id: uuid.UUID, ip: str | None = None
) -> None:
    if source.object_key:
        await delete_object(source.object_key)
    await audit.record(
        session,
        action="data_source.delete",
        org_id=source.org_id,
        actor_user_id=user_id,
        target_type="data_source",
        target_id=source.id,
        ip=ip,
    )
    await session.delete(source)
    await session.commit()


CONTENT_URL_TTL = 300


async def content_url(source: DataSource) -> ContentUrl:
    """Resolve a data source to something openable, per type.

    A file gets a short-lived presigned URL; a url source just hands back the
    address it was created from; pasted text has no target at all and says so
    rather than returning a link that goes nowhere.
    """
    if source.type == DataSourceType.file and source.object_key:
        kind = serve_kind(source.name, str((source.config or {}).get("content_type", "")))
        url = await presigned_get_url(
            source.object_key,
            content_type=kind.serve_type,
            disposition=content_disposition(kind, source.name),
            expires_in=CONTENT_URL_TTL,
        )
        return ContentUrl(url=url, expires_in=CONTENT_URL_TTL, kind="file")
    if source.type == DataSourceType.url and source.uri:
        return ContentUrl(url=source.uri, expires_in=0, kind="url")
    return ContentUrl(url=None, expires_in=0, kind="none")


__all__ = [
    "CONTENT_URL_TTL",
    "content_url",
    "counts_for",
    "create_text",
    "create_upload",
    "create_url",
    "delete",
    "get",
    "list_for_assistant",
    "page_for_assistant",
    "reindex",
    "to_summary",
]
