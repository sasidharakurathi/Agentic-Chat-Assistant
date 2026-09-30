"""The Arq connection pool used to *enqueue* jobs from the API process.

Distinct from ``app/worker.py``, which *runs* jobs in a separate process —
this module is imported by request handlers (``app/services/data_sources.py``),
that one by the ``arq`` CLI.
"""

from __future__ import annotations

import uuid

from arq import ArqRedis, create_pool
from arq.connections import RedisSettings

from app.config import settings
from app.logging import get_logger

log = get_logger(__name__)


class _State:
    pool: ArqRedis | None = None


_state = _State()


async def get_queue() -> ArqRedis:
    if _state.pool is None:
        _state.pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    return _state.pool


async def enqueue_ingest(data_source_id: uuid.UUID) -> bool:
    """Queue an ingestion; False if the queue could not be reached.

    A queue outage doesn't fail the request that created or edited the data
    source (the upload is saved either way), but the caller must not leave
    the source saying "pending": nothing would ever pick it up. See
    ``services.data_sources._queue``."""
    try:
        queue = await get_queue()
        await queue.enqueue_job("ingest_data_source_job", str(data_source_id))
    except Exception as exc:
        log.warning("enqueue_ingest_failed", data_source_id=str(data_source_id), error=str(exc))
        _state.pool = None  # reconnect next time rather than reuse a broken pool
        return False
    return True


async def enqueue_schema_refresh(connection_id: uuid.UUID) -> None:
    """Best-effort, like ingestion: if the queue is down, the agent's next
    introspection refreshes the (then missing or stale) cache itself."""
    try:
        queue = await get_queue()
        await queue.enqueue_job("refresh_schema_job", str(connection_id))
    except Exception as exc:
        log.warning(
            "enqueue_schema_refresh_failed", connection_id=str(connection_id), error=str(exc)
        )


__all__ = ["enqueue_ingest", "enqueue_schema_refresh", "get_queue"]


async def enqueue_summary(conversation_id: uuid.UUID, version: int) -> bool:
    """Queue a conversation summary (task 5.2); False if the queue is down.

    The job id is per summary *version*, so the turns that keep crossing the
    threshold while one summary is pending queue it once, not once each; the
    next version gets a new id once this one is written. A missed summary is
    harmless: the next turn over the threshold queues it again."""
    try:
        queue = await get_queue()
        await queue.enqueue_job(
            "summarize_conversation_job",
            str(conversation_id),
            version,
            _job_id=f"summarize:{conversation_id}:{version}",
        )
    except Exception as exc:
        log.warning("enqueue_summary_failed", conversation_id=str(conversation_id), error=str(exc))
        _state.pool = None
        return False
    return True
