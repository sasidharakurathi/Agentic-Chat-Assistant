"""The Arq worker process. Run it with:

    arq app.worker.WorkerSettings

(from ``apps/api``, same as ``uvicorn app.main:app`` — see ``scripts/dev-worker.ps1``.)

Each job opens its own DB session, same pattern as the SSE chat endpoint
(``app/api/routes/conversations.py``) — a worker job and an HTTP request are
both "some code that needs a session for a while," not tied to a shared one.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, ClassVar

from arq import cron, func
from arq.connections import RedisSettings

from app.config import settings
from app.db.session import get_sessionmaker
from app.logging import configure_logging, get_logger
from app.observability.otel import setup_tracing, shutdown_tracing, span
from app.rag.ingest import ingest_data_source
from app.services import conversation_memory

log = get_logger(__name__)


async def startup(_ctx: dict[str, Any]) -> None:
    # The API configures these at import (app/main.py); the worker is its own
    # process and did neither, so its log lines were unformatted stdlib
    # records without the redaction processor, and it sent no spans.
    configure_logging(settings.log_level, settings.log_format)
    # The arq CLI installs its own handler on its logger before this runs;
    # left there, every arq line printed twice (plain, then structured).
    logging.getLogger("arq").handlers = []
    logging.getLogger("arq").propagate = True
    setup_tracing(service="assistant-studio-worker")
    log.info("worker_starting")


async def shutdown(_ctx: dict[str, Any]) -> None:
    shutdown_tracing()
    log.info("worker_stopping")


async def ingest_data_source_job(ctx: dict[str, Any], data_source_id: str) -> None:
    with span("job.ingest_data_source", data_source_id=data_source_id):
        async with get_sessionmaker()() as session:
            await ingest_data_source(session, uuid.UUID(data_source_id))


async def refresh_schema_job(ctx: dict[str, Any], connection_id: str) -> None:
    """Introspect a connection and rebuild its (permission-filtered) schema
    cache. Queued after a connection is created or changed (plan 6.2)."""
    from app.api.errors import AppError
    from app.models.integration import DbConnection
    from app.services import db_connections as db_svc

    async with get_sessionmaker()() as session:
        conn = await session.get(DbConnection, uuid.UUID(connection_id))
        if conn is None:
            return  # deleted since it was queued
        try:
            await db_svc.refresh_schema(session, conn)
        except AppError as exc:
            # Recorded on the connection (status=error) by refresh_schema.
            log.info("schema_refresh_failed", connection_id=connection_id, error=exc.message)


#: How many remote MCP servers the sweep checks at once.
MCP_SWEEP_CONCURRENCY = 4


async def summarize_conversation_job(
    ctx: dict[str, Any], conversation_id: str, version: int
) -> None:
    """Fold a long conversation's older messages into its summary (task 5.2)."""
    with span("job.summarize_conversation", conversation_id=conversation_id):
        await conversation_memory.summarize(uuid.UUID(conversation_id), version)


async def run_eval_job(ctx: dict[str, Any], run_id: str) -> None:
    """Take an eval suite through the chosen version, case by case (task 6.1)."""
    from app.evals import runner

    with span("job.eval_run", eval_run_id=run_id):
        await runner.run(uuid.UUID(run_id))


async def mcp_health_sweep(ctx: dict[Any, Any], *_args: Any, **_kwargs: Any) -> None:
    """Check every enabled remote (http/sse) MCP server, so the MCP tab shows
    a server that went down without anyone pressing Check (plan §7.2).

    Local-command servers are left out: checking one means starting it, and
    starting every stdio server on a timer would be the runner's heaviest
    work for the least information. They are checked when used or when
    someone presses Check.
    """
    import anyio
    from sqlalchemy import select

    from app.models.integration import McpServer, McpTransport
    from app.services import mcp_discovery

    async with get_sessionmaker()() as session:
        ids = (
            await session.scalars(
                select(McpServer.id).where(
                    McpServer.enabled.is_(True), McpServer.transport != McpTransport.stdio
                )
            )
        ).all()
    limiter = anyio.Semaphore(MCP_SWEEP_CONCURRENCY)

    async def one(server_id: uuid.UUID) -> None:
        async with limiter, get_sessionmaker()() as session:
            server = await session.get(McpServer, server_id)
            if server is not None:
                await mcp_discovery.check(session, server)

    async with anyio.create_task_group() as tg:
        for server_id in ids:
            tg.start_soon(one, server_id)
    log.info("mcp_health_sweep", checked=len(ids))


class WorkerSettings:
    functions: ClassVar = [
        # Arq's default job timeout is 300 s. Indexing a long document on the
        # local CPU embedder takes longer than that, and a timed-out job was
        # simply cancelled mid-way.
        func(ingest_data_source_job, timeout=settings.ingest_job_timeout_s),
        refresh_schema_job,
        summarize_conversation_job,
        func(run_eval_job, timeout=settings.eval_job_timeout_s),
    ]
    cron_jobs: ClassVar = [
        cron(mcp_health_sweep, minute={0, 15, 30, 45}, run_at_startup=False, timeout=600),
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(settings.redis_url)


__all__ = [
    "WorkerSettings",
    "ingest_data_source_job",
    "mcp_health_sweep",
    "refresh_schema_job",
    "run_eval_job",
    "summarize_conversation_job",
]
