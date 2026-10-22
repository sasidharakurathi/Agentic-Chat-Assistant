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
from app.services.mail import MAIL_QUEUE

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


async def recovery_sweep(ctx: dict[Any, Any], *_args: Any, **_kwargs: Any) -> None:
    """Every minute: expire requests nobody decided, remind approvers once,
    settle requests a dead process left behind (Phase 9.1), and leave a
    notice on questions a dead turn never answered (Phase 7a.7)."""
    from app.services import recovery

    await recovery.sweep()


#: How many conversation ids one sweep query asks about.
SWEEP_BATCH = 500


async def agent_files_sweep(ctx: dict[Any, Any], *_args: Any, **_kwargs: Any) -> None:
    """Remove the CLI files no conversation needs (Phase 7a.5): everything of
    a conversation that was deleted or archived, and old transcripts of live
    ones that will never be resumed. Archive and assistant delete remove
    their own; this catches what they missed (a failed delete, an archive
    from before this existed) and the sessions a summary replaced. The API
    and the worker share the files' volume, so this sees the API's turns."""
    from sqlalchemy import select

    from app.agent import cli_files
    from app.models.conversation import Conversation, ConversationStatus

    on_disk = sorted(cli_files.conversation_ids_on_disk())
    keep: dict[uuid.UUID, str | None] = {}
    async with get_sessionmaker()() as session:
        for i in range(0, len(on_disk), SWEEP_BATCH):
            rows = await session.execute(
                select(Conversation.id, Conversation.sdk_session_id).where(
                    Conversation.id.in_(on_disk[i : i + SWEEP_BATCH]),
                    Conversation.status != ConversationStatus.archived,
                )
            )
            keep.update(rows.tuples().all())
    cli_files.sweep(keep)


async def review_fixed_sweep(ctx: dict[Any, Any], *_args: Any, **_kwargs: Any) -> None:
    """Tell people that what they reported was fixed (Phase 10.3): items in
    the needs-review queue whose test case now passes on the live version.
    Each person once."""
    from app.services import review_queue

    told = await review_queue.fixed_sweep()
    if told:
        log.info("review_fixed_sweep", told=told)


async def retention_sweep(ctx: dict[Any, Any], *_args: Any, **_kwargs: Any) -> None:
    """Erase for good the conversations quiet for longer than each
    organisation keeps them (Phase 8.10), in small batches. Held ones stay.

    First, the screenshots attached more than a day ago and never sent
    (Phase 9.6): nothing will ever send them, and otherwise they would stay
    until their conversation is erased. A failure there is logged and the
    retention sweep still runs."""
    from app.services import attachments, retention

    try:
        async with get_sessionmaker()() as session:
            await attachments.sweep_unsent(session)
    except Exception:
        log.exception("unsent_attachments_sweep_failed")
    await retention.retention_sweep()


async def knowledge_sweep(ctx: dict[Any, Any], *_args: Any, **_kwargs: Any) -> None:
    """Remove document copies kept for versions that no longer need them
    (Phase 7b.1): a version rejected, withdrawn or rolled back gives up the
    copy it was approved with. A reindex sweeps its own source; this
    catches the copies a review decision freed."""
    from app.services import manifest

    async with get_sessionmaker()() as session:
        removed = await manifest.sweep_superseded(session)
        await session.commit()
    if removed:
        log.info("knowledge_sweep", removed=removed)


async def send_mail_job(ctx: dict[Any, Any], message_id: str, data: dict) -> None:
    """Deliver one email (Phase 7b.5): retried with a growing wait while the
    failure may pass, recorded as failed when it won't or the attempts run
    out."""
    from arq import Retry

    from app.services import mail

    attempt = int(ctx.get("job_try", 1))
    if not await mail.deliver(uuid.UUID(message_id), data, attempt=attempt):
        raise Retry(defer=mail.retry_delay_s(attempt))


async def execute_request_job(ctx: dict[Any, Any], approval_id: str) -> None:
    """Run one approved request (Phase 9.1). Tried once: its claim makes a
    second delivery a no-op, and a write is never retried by itself."""
    from app.services import executor

    await executor.execute(uuid.UUID(approval_id))


async def audit_seal(ctx: dict[Any, Any], *_args: Any, **_kwargs: Any) -> None:
    """Seal the audit rows written since the last run into the hash chain
    (Phase 7b.7)."""
    from app.services import audit_chain

    async with get_sessionmaker()() as session:
        await audit_chain.seal(session)


class MailWorkerSettings:
    """The interactive queue's worker (`arq app.worker.MailWorkerSettings`):
    email, and since Phase 9.1 approved requests. A slow SMTP server, or a
    backlog, never holds up indexing and evals, and they never hold up an
    invite or a request someone is waiting on."""

    queue_name = MAIL_QUEUE
    functions: ClassVar = [
        func(send_mail_job, max_tries=settings.mail_max_tries, timeout=60),
        func(execute_request_job, max_tries=1, timeout=120),
    ]
    max_jobs = 4
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(settings.redis_url)


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
        cron(agent_files_sweep, hour={3}, minute={30}, run_at_startup=False, timeout=600),
        cron(knowledge_sweep, hour={3}, minute={45}, run_at_startup=False, timeout=600),
        cron(retention_sweep, hour={2}, minute={30}, run_at_startup=False, timeout=1800),
        cron(review_fixed_sweep, minute=set(range(0, 60, 5)), run_at_startup=False, timeout=240),
        # Every minute, starting at once: a restart is exactly when it matters.
        cron(recovery_sweep, run_at_startup=True, timeout=55),
        cron(audit_seal, run_at_startup=True, timeout=55),
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(settings.redis_url)


__all__ = [
    "MailWorkerSettings",
    "WorkerSettings",
    "agent_files_sweep",
    "audit_seal",
    "ingest_data_source_job",
    "knowledge_sweep",
    "mcp_health_sweep",
    "recovery_sweep",
    "refresh_schema_job",
    "review_fixed_sweep",
    "run_eval_job",
    "send_mail_job",
    "summarize_conversation_job",
]
