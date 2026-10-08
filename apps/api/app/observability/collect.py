"""The metrics that are read, not observed (task 6.4): totals from the
database and the queue's length from Redis, at scrape time.

The ledger (`usage_events`), the run and tool-call rows, the sources and
the MCP servers already record everything these report, in every process
and across restarts. Counting them on request means the worker's
ingestions and eval runs are included without the worker exposing anything,
and a restart doesn't zero a spend total.

One short-lived session, a handful of grouped counts, cached for a few
seconds so a scrape every 15 s (or three Prometheus replicas) costs the
database almost nothing.

A source that can't be read (Redis down, say) leaves its metrics out and
is named in `assistant_studio_scrape_errors`, so a missing series is
visibly a failed read and not a zero.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.pool import QueuePool

from app.config import settings
from app.db.redis import get_redis
from app.db.session import get_engine, get_sessionmaker
from app.logging import get_logger
from app.models.approval import Approval, ApprovalStatus
from app.models.assistant import Assistant
from app.models.budget import Budget
from app.models.conversation import Run, ToolCall
from app.models.evals import EvalRun
from app.models.integration import McpServer
from app.models.rag import Chunk, DataSource
from app.models.usage import UsageEvent
from app.observability.metrics import Sample, short_tool
from app.services import budgets, chat

log = get_logger(__name__)

#: How long one collection is reused.
CACHE_S = 10.0
#: Arq's default queue, a sorted set of job ids.
ARQ_QUEUE = "arq:queue"

_cache: dict[str, Any] = {"at": 0.0, "samples": []}

Collector = Callable[[AsyncSession], Awaitable[list[Sample]]]


def _text(value: Any) -> str:
    """An enum's value, or the thing itself."""
    return str(getattr(value, "value", value))


async def _usage(session: AsyncSession) -> list[Sample]:
    rows = (
        await session.execute(
            select(
                UsageEvent.kind,
                UsageEvent.model,
                func.coalesce(func.sum(UsageEvent.tokens_in), 0),
                func.coalesce(func.sum(UsageEvent.tokens_out), 0),
                func.coalesce(func.sum(UsageEvent.cost_usd), 0),
            ).group_by(UsageEvent.kind, UsageEvent.model)
        )
    ).all()
    tokens = Sample(
        "assistant_studio_tokens_total",
        "Tokens used, by kind of call, model and direction.",
        "counter",
        ("kind", "model", "direction"),
    )
    cost = Sample(
        "assistant_studio_cost_usd_total",
        "Spend in USD, by kind of call and model.",
        "counter",
        ("kind", "model"),
    )
    for kind, model, tin, tout, usd in rows:
        labels = (_text(kind), str(model or "unknown"))
        tokens.values.append(((*labels, "in"), float(tin)))
        tokens.values.append(((*labels, "out"), float(tout)))
        cost.values.append((labels, float(usd)))
    by_assistant = Sample(
        "assistant_studio_assistant_cost_usd_total",
        "Spend in USD for each assistant (setup helpers and deleted assistants excluded).",
        "counter",
        ("assistant_id", "assistant"),
    )
    spent = (
        await session.execute(
            select(Assistant.id, Assistant.name, func.coalesce(func.sum(UsageEvent.cost_usd), 0))
            .join(Assistant, Assistant.id == UsageEvent.assistant_id)
            .group_by(Assistant.id, Assistant.name)
        )
    ).all()
    for assistant_id, name, usd in spent:
        by_assistant.values.append(((str(assistant_id), str(name)), float(usd)))
    return [tokens, cost, by_assistant]


async def _runs(session: AsyncSession) -> list[Sample]:
    runs = Sample(
        "assistant_studio_runs_total", "Agent turns, by how they ended.", "counter", ("status",)
    )
    for status, n in (
        await session.execute(select(Run.status, func.count()).group_by(Run.status))
    ).all():
        runs.values.append(((_text(status),), float(n)))

    calls = Sample(
        "assistant_studio_tool_calls_total",
        "Tool calls, by tool and outcome.",
        "counter",
        ("tool", "status"),
    )
    for name, status, n in (
        await session.execute(
            select(ToolCall.tool_name, ToolCall.status, func.count()).group_by(
                ToolCall.tool_name, ToolCall.status
            )
        )
    ).all():
        calls.values.append(((short_tool(str(name)), str(status or "unknown")), float(n)))

    evals = Sample("assistant_studio_eval_runs", "Eval runs, by status.", "gauge", ("status",))
    for status, n in (
        await session.execute(select(EvalRun.status, func.count()).group_by(EvalRun.status))
    ).all():
        evals.values.append(((_text(status),), float(n)))
    return [runs, calls, evals]


async def _knowledge(session: AsyncSession) -> list[Sample]:
    sources = Sample(
        "assistant_studio_data_sources", "Knowledge-base sources, by status.", "gauge", ("status",)
    )
    for status, n in (
        await session.execute(select(DataSource.status, func.count()).group_by(DataSource.status))
    ).all():
        sources.values.append(((_text(status),), float(n)))
    chunks = Sample("assistant_studio_chunks", "Chunks in the index.", "gauge", ())
    chunks.values.append(((), float(await session.scalar(select(func.count(Chunk.id))) or 0)))
    return [sources, chunks]


async def _integrations(session: AsyncSession) -> list[Sample]:
    servers = Sample(
        "assistant_studio_mcp_servers",
        "Enabled MCP servers, by their last check.",
        "gauge",
        ("status",),
    )
    for status, n in (
        await session.execute(
            select(McpServer.status, func.count())
            .where(McpServer.enabled.is_(True))
            .group_by(McpServer.status)
        )
    ).all():
        servers.values.append(((_text(status),), float(n)))
    pending = Sample(
        "assistant_studio_approvals_pending", "Tool calls waiting for a person.", "gauge", ()
    )
    # Past its expiry a row can't be decided (Phase 7a.7): not waiting.
    waiting = await session.scalar(
        select(func.count(Approval.id)).where(
            Approval.status == ApprovalStatus.pending,
            or_(Approval.expires_at.is_(None), Approval.expires_at > datetime.now(UTC)),
        )
    )
    pending.values.append(((), float(waiting or 0)))
    return [servers, pending]


async def _budgets(session: AsyncSession) -> list[Sample]:
    """Each budget as a ratio of its limit: 0.8 is where chats are warned,
    1.0 where they stop. Labelled by org so an alert can say whose."""
    used = Sample(
        "assistant_studio_budget_used_ratio",
        "Spend over limit for each budget in its current period.",
        "gauge",
        ("org_id", "scope", "period"),
    )
    org_ids = (await session.scalars(select(Budget.org_id).distinct())).all()
    for org_id in org_ids:
        for status in await budgets.list_for_org(session, org_id):
            scope = "org" if status.assistant_id is None else f"assistant:{status.assistant_id}"
            used.values.append(((str(org_id), scope, status.period.value), round(status.ratio, 4)))
    return [used]


async def _queue(_session: AsyncSession) -> list[Sample]:
    depth = Sample(
        "assistant_studio_queue_depth",
        "Background jobs waiting for a worker (ingestion, evals, summaries).",
        "gauge",
        (),
    )
    depth.values.append(((), float(await get_redis().zcard(ARQ_QUEUE))))
    return [depth]


def process() -> list[Sample]:
    """This API process's own load: its turn slots and its connection pool.
    Unlike the rest of this file these are per process, not platform-wide,
    and they are read from memory, so they are never cached: a pool that
    filled for two seconds shows in the scrape that lands in them."""
    turns = Sample(
        "assistant_studio_turns",
        "Agent turns in this process: running, or waiting for a free slot.",
        "gauge",
        ("state",),
    )
    turns.values += [(("running",), float(chat.turn_load["running"]))]
    turns.values += [(("waiting",), float(chat.turn_load["waiting"]))]
    slots = Sample(
        "assistant_studio_turn_slots",
        "How many turns may run at once (AGENT_MAX_CONCURRENCY).",
        "gauge",
        (),
    )
    slots.values.append(((), float(settings.agent_max_concurrency)))
    pool = get_engine().pool
    in_use = Sample(
        "assistant_studio_db_pool_connections",
        "Database connections in this process's pool: in use, or idle.",
        "gauge",
        ("state",),
    )
    limit = Sample(
        "assistant_studio_db_pool_limit",
        "The most connections this process may open (pool size plus overflow).",
        "gauge",
        (),
    )
    if isinstance(pool, QueuePool):
        in_use.values += [(("in_use",), float(pool.checkedout()))]
        in_use.values += [(("idle",), float(pool.checkedin()))]
        limit.values.append(((), float(settings.db_pool_size + settings.db_max_overflow)))
    return [turns, slots, in_use, limit]


COLLECTORS: dict[str, Collector] = {
    "usage": _usage,
    "runs": _runs,
    "knowledge": _knowledge,
    "integrations": _integrations,
    "budgets": _budgets,
    "queue": _queue,
}


async def collect(*, fresh: bool = False) -> list[Sample]:
    """Every read metric, from the cache when it is recent."""
    now = time.monotonic()
    if not fresh and _cache["samples"] and now - _cache["at"] < CACHE_S:
        return [*_cache["samples"], *process()]

    samples: list[Sample] = []
    errors = Sample(
        "assistant_studio_scrape_errors",
        "1 for each group of metrics that could not be read in this scrape.",
        "gauge",
        ("source",),
    )
    async with get_sessionmaker()() as session:
        for name, collector in COLLECTORS.items():
            try:
                samples += await collector(session)
                errors.values.append(((name,), 0.0))
            except Exception as exc:
                log.warning("metrics_collect_failed", source=name, error=type(exc).__name__)
                await session.rollback()
                errors.values.append(((name,), 1.0))
    samples.append(errors)
    _cache.update(at=now, samples=samples)
    return [*samples, *process()]


def clear_cache() -> None:
    _cache.update(at=0.0, samples=[])


__all__ = ["ARQ_QUEUE", "CACHE_S", "COLLECTORS", "clear_cache", "collect"]
