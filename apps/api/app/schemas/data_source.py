from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.models.rag import DataSourceStatus, DataSourceType
from app.schemas.common import ApiModel, ORMModel


class CreateUrlSource(BaseModel):
    type: Literal["url"] = "url"
    name: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=1, max_length=2000)


class CreateTextSource(BaseModel):
    type: Literal["text"] = "text"
    name: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=200_000)


DataSourceCreate = Annotated[CreateUrlSource | CreateTextSource, Field(discriminator="type")]


class IngestProgress(ApiModel):
    """Where a running ingestion is (``app.rag.progress``)."""

    stage: str
    done: int | None = None
    total: int | None = None


class IngestReportOut(ApiModel):
    """What the last ingestion did (``app.rag.ingest.IngestReport``)."""

    chunks: int = 0
    embedding_model: str = ""
    embedded: int = 0
    reused_embeddings: int = 0
    contextualized: int = 0
    reused_context: int = 0
    context_failed: int = 0
    context_skipped: str | None = None
    cost_usd: float = 0.0


class DataSourceSummary(ORMModel):
    id: uuid.UUID
    assistant_id: uuid.UUID
    type: DataSourceType
    name: str
    uri: str | None
    bytes: int | None
    status: DataSourceStatus
    error: str | None
    created_at: datetime
    indexed_at: datetime | None
    # What ingestion actually produced. Default 0 because that is the honest
    # answer for a source that hasn't been indexed yet — which is exactly the
    # state every create/upload/reindex response is in.
    document_count: int = 0
    chunk_count: int = 0
    ingest_report: IngestReportOut | None = None
    #: Only while the source is being processed.
    progress: IngestProgress | None = None


class ContentUrl(ApiModel):
    """Where a citation's "Open" link should point.

    ``url`` is null for a source with nothing to open — pasted text has no
    file and no address. The panel shows the snippet in that case rather than
    a dead link.
    """

    url: str | None = None
    expires_in: int = 0
    kind: Literal["file", "url", "none"] = "none"


__all__ = [
    "ContentUrl",
    "CreateTextSource",
    "CreateUrlSource",
    "DataSourceCreate",
    "DataSourceSummary",
]
