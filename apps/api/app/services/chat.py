"""Conversation lifecycle + the turn orchestration behind the SSE endpoint."""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import tempfile
import time
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import approval_registry, interrupts, session_store
from app.agent.approvals import ApprovalRequest
from app.agent.citations import blocks_from
from app.agent.events import (
    AgentEvent,
    ApprovalRequiredEvent,
    CitationEvent,
    DoneEvent,
    ErrorEvent,
)
from app.agent.runtime import Turn
from app.api.errors import NotFound
from app.config import settings
from app.db.pagination import PageResult, keyset_page
from app.db.session import get_sessionmaker
from app.logging import get_logger
from app.models.assistant import Assistant, AssistantVersion
from app.models.conversation import (
    Conversation,
    ConversationStatus,
    Message,
    MessageRole,
    Run,
    RunStatus,
    ToolCall,
)
from app.models.usage import UsageEvent, UsageKind
from app.observability.turn_trace import TurnTrace
from app.schemas.assistant_config import AssistantConfig
from app.services import approvals as approvals_svc
from app.services import audit

log = get_logger(__name__)

_SCRATCH_BASE = Path(tempfile.gettempdir()) / "assistant-studio" / "scratch"


def _scratch_dir(conversation_id: uuid.UUID) -> Path:
    d = _SCRATCH_BASE / str(conversation_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def discard_scratch(conversation_id: uuid.UUID) -> None:
    """Remove a conversation's scratch directory, if it has one."""
    shutil.rmtree(_SCRATCH_BASE / str(conversation_id), ignore_errors=True)


async def create_conversation(
    session: AsyncSession,
    *,
    assistant: Assistant,
    user_id: uuid.UUID,
    title: str | None,
    external_user_ref: str | None = None,
) -> Conversation:
    conv = Conversation(
        assistant_id=assistant.id,
        assistant_version_id=assistant.current_version_id,
        org_id=assistant.org_id,
        created_by=user_id,
        title=(title or "New conversation").strip() or "New conversation",
        external_user_ref=external_user_ref,
    )
    session.add(conv)
    await session.flush()
    await session.commit()
    return conv


async def list_conversations(
    session: AsyncSession,
    assistant_id: uuid.UUID,
    *,
    limit: int = 50,
    cursor: str | None = None,
    external_user_ref: str | None = None,
) -> PageResult[Conversation]:
    stmt = select(Conversation).where(
        Conversation.assistant_id == assistant_id,
        # Archiving used to leave a conversation right here, so "delete"
        # was undone by the next reload.
        Conversation.status != ConversationStatus.archived,
    )
    if external_user_ref is not None:
        stmt = stmt.where(Conversation.external_user_ref == external_user_ref)
    return await keyset_page(
        session,
        stmt,
        [Conversation.created_at, Conversation.id],
        limit=limit,
        cursor=cursor,
    )


async def list_runs(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    *,
    message_id: uuid.UUID | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> PageResult[Run]:
    """Runs were write-only: recorded on every turn, read by nothing."""
    stmt = select(Run).where(Run.conversation_id == conversation_id)
    if message_id is not None:
        stmt = stmt.where(Run.message_id == message_id)
    return await keyset_page(session, stmt, [Run.created_at, Run.id], limit=limit, cursor=cursor)


async def get_run(
    session: AsyncSession, conversation_id: uuid.UUID, run_id: uuid.UUID
) -> tuple[Run, Message | None]:
    run = await session.get(Run, run_id)
    # Scoped to the conversation the caller was authorised for: a run id from
    # another conversation is "not found", never a different tenant's run.
    if run is None or run.conversation_id != conversation_id:
        raise NotFound("Run not found")
    msg = await session.get(Message, run.message_id) if run.message_id else None
    return run, msg


def run_steps(message: Message | None) -> list[dict[str, Any]]:
    """The tool calls a run made, from its message's blocks. Rows written
    before blocks were typed have no "type" key; they are tool calls too."""
    if message is None:
        return []
    return [
        b
        for b in (message.blocks or [])
        if isinstance(b, dict) and b.get("type", "tool_call") == "tool_call"
    ]


async def load(session: AsyncSession, conversation_id: uuid.UUID) -> Conversation:
    conv = await session.get(Conversation, conversation_id)
    if conv is None:
        raise NotFound("Conversation not found")
    return conv


async def list_messages(
    session: AsyncSession, conversation_id: uuid.UUID, *, limit: int = 50, cursor: str | None = None
) -> PageResult[Message]:
    """Pages walk *backwards* from the newest message, since a chat opens at
    the bottom and loads history on demand. Each page's items are returned
    oldest-first, so a client can prepend a page as it is."""
    page = await keyset_page(
        session,
        select(Message).where(Message.conversation_id == conversation_id),
        [Message.created_at, Message.id],
        limit=limit,
        cursor=cursor,
    )
    page.items.reverse()
    return page


async def rename(
    session: AsyncSession,
    conv: Conversation,
    title: str,
    *,
    actor_user_id: uuid.UUID | None = None,
    ip: str | None = None,
) -> Conversation:
    old_title = conv.title
    conv.title = title.strip() or conv.title
    await audit.record(
        session,
        action="conversation.rename",
        org_id=conv.org_id,
        actor_user_id=actor_user_id,
        target_type="conversation",
        target_id=conv.id,
        meta={"from": old_title, "to": conv.title},
        ip=ip,
    )
    await session.flush()
    await session.commit()
    return conv


async def archive(
    session: AsyncSession,
    conv: Conversation,
    *,
    actor_user_id: uuid.UUID | None = None,
    ip: str | None = None,
) -> None:
    conv.status = ConversationStatus.archived
    await audit.record(
        session,
        action="conversation.archive",
        org_id=conv.org_id,
        actor_user_id=actor_user_id,
        target_type="conversation",
        target_id=conv.id,
        ip=ip,
    )
    await session.flush()
    await session.commit()


async def _config_for(session: AsyncSession, conv: Conversation) -> AssistantConfig:
    if conv.assistant_version_id is not None:
        version = await session.get(AssistantVersion, conv.assistant_version_id)
        if version is not None:
            return AssistantConfig.model_validate(version.config)
    assistant = await session.get(Assistant, conv.assistant_id)
    assert assistant is not None
    return AssistantConfig.model_validate(assistant.draft_config)


async def _raise_approval(
    conv: Conversation,
    emit: Callable[[AgentEvent], None],
    tool_name: str,
    tool_input: dict,
    risk: str,
    rationale: str,
    interrupted: asyncio.Event | None = None,
    *,
    tool_use_id: str | None = None,
) -> str:
    """Create a pending approval, announce it on the stream, and block.

    Its own session, deliberately: this runs *inside* the turn while the
    outer session is mid-stream, and the pending row has to be visible to the
    resolve endpoint — a different request, possibly on a different worker —
    the moment it is announced.
    """
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as s:
        row = await approvals_svc.create(
            s,
            conversation_id=conv.id,
            org_id=conv.org_id,
            tool_name=tool_name,
            tool_input=tool_input,
            risk=risk,
            rationale=rationale,
            # The setting existed and nothing read it: every approval used
            # the hard-coded 300 s, whatever APPROVAL_TIMEOUT_S said.
            timeout_s=settings.approval_timeout_s,
            tool_call_id=tool_use_id,
        )
    # Claim the waiter BEFORE announcing, so a decision landing between the
    # insert and the await is not lost.
    future = approval_registry.register(row.id)
    emit(
        ApprovalRequiredEvent(
            approval_id=str(row.id),
            tool=tool_name,
            input=dict(row.input),
            risk=row.risk.value,
            rationale=rationale,
            expires_at=row.expires_at.isoformat() if row.expires_at else None,
        )
    )
    # Stop must work while a reviewer is still deciding. The driver is parked
    # inside this very wait, so it can never notice the interrupt itself —
    # before this, Stop on a pending approval did nothing until the runtime's
    # grace period ran out and force-cancelled the turn five seconds later.
    watcher = (
        asyncio.create_task(_settle_on_interrupt(future, interrupted))
        if interrupted is not None
        else None
    )
    try:
        decision = await approval_registry.wait(row.id, future, timeout=settings.approval_timeout_s)
    except asyncio.CancelledError:
        # The turn is gone (the client disconnected) while the reviewer was
        # still deciding. Left `pending`, the row would keep being offered on
        # reload, and approving it would record a decision for a statement
        # that can no longer run. `expire` only moves a row out of pending, so
        # a decision that raced in first is never overwritten.
        with anyio.CancelScope(shield=True):
            await approvals_svc.expire(row.id)
        raise
    finally:
        if watcher is not None:
            watcher.cancel()
    if decision in ("expired", "interrupted"):
        # An interrupted approval is closed like an unanswered one: it leaves
        # the pending list, and a late click cannot approve a dead statement.
        await approvals_svc.expire(row.id)
    return decision


async def _settle_on_interrupt(
    future: asyncio.Future[approval_registry.Decision], interrupted: asyncio.Event
) -> None:
    await interrupted.wait()
    if not future.done():
        future.set_result("interrupted")


#: One semaphore per event loop: asyncio primitives bind to the loop that
#: first waits on them, and tests run a fresh loop each.
_slots: dict[int, asyncio.Semaphore] = {}


def _turn_slots() -> asyncio.Semaphore:
    key = id(asyncio.get_running_loop())
    if key not in _slots:
        _slots[key] = asyncio.Semaphore(settings.agent_max_concurrency)
    return _slots[key]


async def run_message(
    session: AsyncSession, *, conversation_id: uuid.UUID, text: str
) -> AsyncGenerator[AgentEvent, None]:
    conv = await load(session, conversation_id)
    config = await _config_for(session, conv)

    cap = config.models.main.max_budget_usd
    spent = float(conv.cost_usd)
    if cap is not None and spent >= cap:
        yield ErrorEvent(
            code="budget_exceeded", message="This conversation has reached its spend limit."
        )
        return

    # Bounded concurrency (plan §4.1): each turn runs a CLI subprocess, so an
    # unbounded burst of messages is an unbounded burst of processes. The
    # setting existed and nothing read it.
    slots = _turn_slots()
    try:
        await asyncio.wait_for(slots.acquire(), timeout=settings.agent_queue_wait_s)
    except TimeoutError:
        yield ErrorEvent(
            code="busy",
            message="Too many conversations are running right now. Try again in a moment.",
        )
        return
    try:
        async with contextlib.aclosing(
            _run_admitted(session, conv, config, text, cap, spent)
        ) as events:
            async for ev in events:
                yield ev
    finally:
        slots.release()


async def _run_admitted(
    session: AsyncSession,
    conv: Conversation,
    config: AssistantConfig,
    text: str,
    cap: float | None,
    spent: float,
) -> AsyncGenerator[AgentEvent, None]:
    """One turn, once it holds a concurrency slot."""
    user_msg = Message(
        conversation_id=conv.id,
        org_id=conv.org_id,
        role=MessageRole.user,
        content=text,
    )
    session.add(user_msg)
    await session.flush()
    await session.commit()  # keep the user's message even if the client disconnects

    resume_id = await _session_to_resume(conv)
    remaining = None if cap is None else max(0.0, cap - spent)

    def request_approval(
        tool_name: str,
        tool_input: dict,
        risk: str,
        rationale: str,
        *,
        tool_use_id: str | None = None,
    ) -> Awaitable[str]:
        # `turn` is referenced lazily: closures capture the variable, and this
        # is only ever called from inside `turn.stream()`.
        return _raise_approval(
            conv,
            turn.emit,
            tool_name,
            tool_input,
            risk,
            rationale,
            turn.interrupted,
            tool_use_id=tool_use_id,
        )

    turn = Turn(
        config,
        prompt=text,
        assistant_id=conv.assistant_id,
        session_id=resume_id,
        budget_remaining_usd=remaining,
        scratch_dir=_scratch_dir(conv.id),
        citation_state=dict(conv.citation_state or {}),
        approvals=ApprovalRequest(
            conversation_id=conv.id, org_id=conv.org_id, request=request_approval
        ),
    )
    started = time.perf_counter()
    # One id for the whole turn: stored on the run row, and bound into every
    # log line written while it runs, including from the driver and tool
    # tasks, which copy this context when they are created.
    # The turn's span: under the request's trace, so the id stored on the run
    # finds it in the tracing backend (and in Langfuse, the conversation).
    tracing = TurnTrace.start(
        conversation_id=conv.id,
        assistant_id=conv.assistant_id,
        org_id=conv.org_id,
        user_ref=conv.external_user_ref,
        model=config.models.main.model,
        prompt=text,
    )
    trace_id = tracing.trace_id
    structlog.contextvars.bind_contextvars(trace_id=trace_id, conversation_id=str(conv.id))
    # Reachable by `POST /conversations/{id}:interrupt` for exactly as long as
    # it is streaming (task 1.6).
    stop = turn.interrupt
    interrupts.register(conv.id, stop)
    try:
        # `aclosing` so that an interruption closes the turn's own stream
        # *now*, cancelling the driver (and with it the SDK client and any
        # spend) deterministically, rather than whenever the abandoned inner
        # generator happens to be garbage-collected.
        async with contextlib.aclosing(turn.stream()) as events:
            async for ev in events:
                yield ev
    except (GeneratorExit, asyncio.CancelledError):
        # The client went away mid-turn. Two things arrive here: GeneratorExit
        # when the SSE route stops iterating and closes us, and CancelledError
        # when Starlette cancels the response. Either way the turn still
        # happened — tokens were spent, text streamed, tools may have run — so
        # it is recorded as aborted rather than silently dropped.
        #
        # Shielded because Starlette cancels through an *anyio* scope, which is
        # level-triggered: every await inside a cancelled scope raises again,
        # so an unshielded commit here would itself be cancelled.
        turn.outcome.status = RunStatus.aborted
        turn.outcome.error = "client disconnected"
        with anyio.CancelScope(shield=True):
            try:
                await _finalize(session, conv, config, user_msg, turn, started, spent, trace_id)
            except Exception:
                log.exception("abort_finalize_failed", conversation_id=str(conv.id))
            _end_trace(tracing, turn, started)
        raise
    except Exception as exc:
        # Not recorded as a run (nothing was finalized), but the span is
        # still ended, or it would never be exported.
        turn.outcome.status = RunStatus.error
        turn.outcome.error = f"{type(exc).__name__}: {exc}"
        _end_trace(tracing, turn, started)
        raise
    finally:
        interrupts.unregister(conv.id, stop)
        # By name, not by token: a token must be reset in the context that
        # set it, and a generator can be closed from a different one.
        structlog.contextvars.unbind_contextvars("trace_id", "conversation_id")

    # Shielded on the normal path too. A disconnect does not have to land
    # mid-stream: when events are already queued nothing suspends, so the
    # first await after a cancellation can be this commit — and the anyio
    # scope, being level-triggered, would cancel it. That lost a *completed*
    # turn (reproduced: `_finalize` interrupted with status still "ok").
    with anyio.CancelScope(shield=True):
        asst_msg, run = await _finalize(
            session, conv, config, user_msg, turn, started, spent, trace_id
        )
    _end_trace(tracing, turn, started)

    # After the commit, deliberately. Every yield before it is a point where a
    # client disconnect could interrupt; emitting citations here adds none.
    for citation in turn.outcome.citations:
        yield CitationEvent(**asdict(citation))

    yield DoneEvent(message_id=str(asst_msg.id), run_id=str(run.id), trace_id=trace_id)


async def _session_to_resume(conv: Conversation) -> str | None:
    """The SDK session this turn continues: the conversation row's, always.

    The Redis entry is a cache of that column, and it used to win: `cached or
    row`. A cache write that failed during a Redis blip left the previous id
    there, and the next turn resumed the older session, silently losing
    everything since. The row is loaded for every turn anyway, so it decides.
    A cache that disagrees is only logged: `_finalize` rewrites it after the
    turn's commit.
    """
    cached = await session_store.get(conv.id)
    if cached is not None and cached != conv.sdk_session_id:
        log.warning("session_cache_stale", conversation_id=str(conv.id))
    return conv.sdk_session_id


def _end_trace(tracing: TurnTrace, turn: Turn, started: float) -> None:
    duration_ms = int((time.perf_counter() - started) * 1000)
    tracing.finish(turn.outcome, duration_ms=duration_ms, model_calls=turn.model_calls)


def _tool_server(tool_name: str) -> str:
    """`mcp__caps__sql_query` -> "caps"; `WebSearch` -> "builtin"."""
    prefix, _, rest = tool_name.partition("__")
    server, sep, _ = rest.partition("__")
    return server if prefix == "mcp" and sep else "builtin"


async def _finalize(
    session: AsyncSession,
    conv: Conversation,
    config: AssistantConfig,
    user_msg: Message,
    turn: Turn,
    started: float,
    spent: float,
    trace_id: str | None = None,
) -> tuple[Message, Run]:
    """Persist a turn: the assistant message, its run row, the usage event and
    the conversation rollups, in one commit.

    Shared by the normal path and the disconnect path, so an interrupted turn
    is recorded by exactly the same code as a finished one — just with the
    text it had reached and status `aborted`.
    """
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    o = turn.outcome

    # Resolve against the exact string being persisted — the recorded marker
    # offsets are offsets into *this*, and `.strip()` would shift them.
    content = o.text.strip()
    try:
        o.citations = turn.resolve_citations(content)
    except Exception:
        # Never let citation resolution cost the user an answer they already
        # watched stream in. A turn with no sources panel is a degraded turn;
        # a lost message, run row and unrecorded spend is a broken one.
        log.exception("citation_resolution_failed", conversation_id=str(conv.id))
        o.citations = []

    asst_msg = Message(
        conversation_id=conv.id,
        org_id=conv.org_id,
        role=MessageRole.assistant,
        content=content,
        # Typed blocks: tool calls keep their existing shape (plus an explicit
        # discriminator from here on) and citations sit alongside them. Rows
        # written before 2.9 have no "type" key at all, so readers must treat
        # "absent" as a tool call rather than filtering for type == tool_call.
        blocks=[{"type": "tool_call", **call} for call in o.tool_calls] + blocks_from(o.citations),
        model=config.models.main.model,
        tokens_in=o.tokens_in,
        tokens_out=o.tokens_out,
        latency_ms=elapsed_ms,
        parent_id=user_msg.id,
    )
    session.add(asst_msg)
    await session.flush()

    run = Run(
        conversation_id=conv.id,
        message_id=asst_msg.id,
        org_id=conv.org_id,
        model=config.models.main.model,
        effort=config.models.main.effort,
        driver=o.driver_name,
        num_turns=o.num_turns,
        tokens_in=o.tokens_in,
        tokens_out=o.tokens_out,
        cost_usd=o.cost_usd,
        duration_ms=elapsed_ms,
        status=o.status,
        error=o.error,
        trace_id=trace_id,
    )
    session.add(run)

    session.add(
        UsageEvent(
            org_id=conv.org_id,
            assistant_id=conv.assistant_id,
            conversation_id=conv.id,
            kind=UsageKind.llm,
            model=config.models.main.model,
            tokens_in=o.tokens_in,
            tokens_out=o.tokens_out,
            cost_usd=o.cost_usd,
        )
    )

    for call in o.tool_calls:
        name = str(call.get("name", ""))
        session.add(
            ToolCall(
                message_id=asst_msg.id,
                conversation_id=conv.id,
                org_id=conv.org_id,
                call_id=call.get("id"),
                tool_name=name,
                server=_tool_server(name),
                input=call.get("input") or {},
                output=call.get("output"),
                status=call.get("status"),
                latency_ms=o.tool_latency_ms.get(str(call.get("id"))),
            )
        )
    if o.web_searches:
        # A count, not a price: the CLI's own total (on the llm row) is taken
        # to include server-tool charges, so pricing it here would double it.
        session.add(
            UsageEvent(
                org_id=conv.org_id,
                assistant_id=conv.assistant_id,
                conversation_id=conv.id,
                kind=UsageKind.tool,
                model="web_search",
                tokens_in=o.web_searches,
                tokens_out=0,
                cost_usd=0,
            )
        )

    # Retrieval's own spend (query embedding + rerank): the kinds existed on
    # the ledger and were never written, so kb_search looked free.
    for line in turn.rag_usage.rows():
        session.add(
            UsageEvent(
                org_id=conv.org_id,
                assistant_id=conv.assistant_id,
                conversation_id=conv.id,
                kind=UsageKind(line.kind),
                model=line.model,
                tokens_in=line.tokens,
                tokens_out=0,
                cost_usd=line.cost_usd,
            )
        )

    conv.cost_usd = spent + o.cost_usd + turn.rag_usage.cost_usd
    usage = dict(conv.token_usage or {})
    usage["in"] = int(usage.get("in", 0)) + o.tokens_in
    usage["out"] = int(usage.get("out", 0)) + o.tokens_out
    conv.token_usage = usage
    conv.last_message_at = datetime.now(UTC)
    if o.sdk_session_id and o.sdk_session_id != conv.sdk_session_id:
        conv.sdk_session_id = o.sdk_session_id
    state = turn.citation_state()
    if state is not None:
        conv.citation_state = state
    await session.flush()
    await session.commit()
    # After the commit, so the cache is never ahead of the row, and on every
    # turn, not only when the id changes: that refills it after an eviction or
    # a Redis restart, and keeps its TTL counting from the last use.
    if conv.sdk_session_id:
        await session_store.set(conv.id, conv.sdk_session_id)
    return asst_msg, run


__all__ = [
    "archive",
    "create_conversation",
    "get_run",
    "list_conversations",
    "list_messages",
    "list_runs",
    "load",
    "rename",
    "run_message",
    "run_steps",
]
