"""Live progress of an ingestion (plan §5.1 step 7, "emit progress events").

A source being indexed reports each stage (fetching, chunking, contextualizing
n/m, embedding n/m, writing) here, and the sources API attaches the latest
report to a source that is still processing, so the page can show "Embedding
3/12" instead of a spinner with no end in sight.

Redis, not the database: ingestion writes a source's chunks in one
transaction (a reindex must never leave a window with no chunks at all), so
progress cannot be committed from inside it. Best-effort like every other
Redis use here: a lost report costs a progress line, never an ingestion.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from app.db.redis import get_redis
from app.logging import get_logger

log = get_logger(__name__)

_KEY = "ingest-progress:"
#: A stale entry from a worker that died mid-job expires on its own.
_TTL_S = 3600


def _key(source_id: uuid.UUID) -> str:
    return f"{_KEY}{source_id}"


async def report(
    source_id: uuid.UUID, stage: str, done: int | None = None, total: int | None = None
) -> None:
    value = json.dumps({"stage": stage, "done": done, "total": total})
    try:
        await get_redis().set(_key(source_id), value, ex=_TTL_S)
    except Exception as exc:
        log.debug("ingest_progress_report_failed", error=str(exc)[:200])


async def read(source_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict[str, Any]]:
    if not source_ids:
        return {}
    try:
        values = await get_redis().mget([_key(i) for i in source_ids])
    except Exception as exc:
        log.debug("ingest_progress_read_failed", error=str(exc)[:200])
        return {}
    out: dict[uuid.UUID, dict[str, Any]] = {}
    for source_id, raw in zip(source_ids, values, strict=True):
        if raw:
            try:
                out[source_id] = json.loads(raw)
            except ValueError:
                continue
    return out


async def clear(source_id: uuid.UUID) -> None:
    try:
        await get_redis().delete(_key(source_id))
    except Exception as exc:
        log.debug("ingest_progress_clear_failed", error=str(exc)[:200])


__all__ = ["clear", "read", "report"]
