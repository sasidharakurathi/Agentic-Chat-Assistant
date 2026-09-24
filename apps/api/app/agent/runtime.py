"""One agent turn: pick a driver, stream events, accumulate the outcome, and
enforce the per-conversation budget mid-stream.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import anyio
from sqlalchemy import select

from app.agent.approvals import ApprovalRequest, build_can_use_tool, redact
from app.agent.citations import Citation, CitationRegistry
from app.agent.driver import get_driver
from app.agent.events import (
    AgentEvent,
    ErrorEvent,
    ThinkingEvent,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
    UsageEvent,
)
from app.agent.options import build_runtime_spec
from app.datasources.sql_guard import Permissions
from app.db.session import get_sessionmaker
from app.logging import get_logger
from app.models.conversation import RunStatus
from app.models.integration import DbConnection
from app.rag import usage as rag_usage
from app.schemas.assistant_config import AssistantConfig

log = get_logger(__name__)

#: Most of a subagent's own text kept with its delegation call.
SUBAGENT_TEXT_CHARS = 20_000

#: Sentinel pushed onto the queue when the driver's stream finishes.
_DONE = object()

#: How long a cancelled turn may take to shut its driver down (close the SDK
#: client, expire a pending approval) before the request stops waiting.
PUMP_SHUTDOWN_TIMEOUT_S = 10.0

#: How long an interrupted driver gets to stop by itself — letting the SDK end
#: the turn cleanly and report the usage it really incurred — before the pump
#: is cancelled outright.
INTERRUPT_GRACE_S = 5.0
#: Pushed onto the queue when that grace period runs out.
_FORCE_STOP = object()


@dataclass
class TurnOutcome:
    text: str = ""
    thinking: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    num_turns: int = 1
    tool_calls: list[dict] = field(default_factory=list)
    status: RunStatus = RunStatus.ok
    error: str | None = None
    driver_name: str = ""
    sdk_session_id: str | None = None
    citations: list[Citation] = field(default_factory=list)
    #: tool call id -> ms from the call to its result.
    tool_latency_ms: dict[str, int] = field(default_factory=dict)
    #: tool call id -> wall-clock ns at the call, for its trace span.
    tool_started_ns: dict[str, int] = field(default_factory=dict)
    web_searches: int = 0


class Turn:
    def __init__(
        self,
        config: AssistantConfig,
        *,
        prompt: str,
        assistant_id: uuid.UUID | None = None,
        session_id: str | None = None,
        budget_remaining_usd: float | None = None,
        scratch_dir: Path | None = None,
        citation_state: dict | None = None,
        approvals: ApprovalRequest | None = None,
    ) -> None:
        self._config = config
        self._prompt = prompt
        self._assistant_id = assistant_id
        self._session_id = session_id
        self._budget_remaining = budget_remaining_usd
        self._scratch_dir = scratch_dir
        self._approvals = approvals
        self.outcome = TurnOutcome()
        #: Model calls observed so far (from per-call usage reports).
        self._model_calls = 0
        #: tool call id -> perf_counter at the call, for latency.
        self._tool_started: dict[str, float] = {}
        #: Embedding and rerank usage from this turn's retrieval.
        self.rag_usage = rag_usage.Meter()
        # ONE queue for both driver output and events raised from inside a
        # tool-permission callback. Two queues would deadlock: `stream()`
        # would block on the driver's queue while the pump task is itself
        # blocked waiting for the approval it just emitted on the other one.
        self._events: asyncio.Queue[object] = asyncio.Queue()
        #: Set by `interrupt()`; the driver watches it.
        self._interrupt = asyncio.Event()
        # Seeded from the conversation, not started fresh: the SDK session is
        # resumed across turns, so the model's context still holds the markers
        # earlier turns handed out. Restarting the numbering would make a
        # stale [1] resolve to a different document.
        self.registry: CitationRegistry | None = (
            CitationRegistry(citation_state)
            if (config.rag.enabled and config.rag.citations)
            else None
        )

    def emit(self, event: AgentEvent) -> None:
        """Push an event into the turn's stream from outside the driver loop.

        `can_use_tool` runs *inside* the driver's iteration, so it cannot
        yield — but an approval that nobody is told about is an approval
        nobody can grant. This is how `approval_required` reaches the client
        while the tool call that raised it is still blocked.
        """
        self._events.put_nowait(event)

    async def _credential_allows(self, connection_id: str, kind: str) -> bool:
        """Does this connection's credential allow `kind` ("write"/"ddl") right
        now? Read fresh each time: permissions can change after publish."""
        try:
            cid = uuid.UUID(connection_id)
        except (TypeError, ValueError):
            return False
        async with get_sessionmaker()() as session:
            conn = await session.get(DbConnection, cid)
        if conn is None or conn.assistant_id != self._assistant_id:
            return False
        perms = Permissions.from_dict(conn.permissions)
        return perms.ddl if kind == "ddl" else perms.write

    @property
    def interrupted(self) -> asyncio.Event:
        """Set once `interrupt()` has been called. Anything the turn blocks on
        for a *human* (an approval) watches this, because the driver cannot:
        it is itself parked inside that wait."""
        return self._interrupt

    def interrupt(self) -> None:
        """Ask the turn to stop (task 1.6). Idempotent.

        The driver is told first, so a cooperative one — the real SDK's
        `client.interrupt()` — can end cleanly and report the usage it really
        incurred. If it has not finished within INTERRUPT_GRACE_S, the pump is
        cancelled outright. Either way the turn completes *normally* from the
        caller's point of view: it is persisted, and the stream ends with a
        `done`, with whatever text had arrived.
        """
        if self._interrupt.is_set():
            return
        self._interrupt.set()
        self.outcome.status = RunStatus.aborted
        self.outcome.error = "interrupted"
        asyncio.get_running_loop().call_later(
            INTERRUPT_GRACE_S, self._events.put_nowait, _FORCE_STOP
        )

    async def _db_engines(self) -> dict[str, str] | None:
        """connection_id -> engine for the wired databases, so each tool
        family is offered only where it can work."""
        if not self._config.databases or self._assistant_id is None:
            return None
        ids = []
        for ref in self._config.databases:
            try:
                ids.append(uuid.UUID(ref.connection_id))
            except ValueError:
                continue
        async with get_sessionmaker()() as session:
            rows = await session.execute(
                select(DbConnection.id, DbConnection.engine).where(
                    DbConnection.id.in_(ids), DbConnection.assistant_id == self._assistant_id
                )
            )
            return {str(cid): engine.value for cid, engine in rows.all()}

    async def stream(self) -> AsyncGenerator[AgentEvent, None]:
        spec = build_runtime_spec(
            self._config,
            assistant_id=self._assistant_id,
            scratch_dir=self._scratch_dir,
            citations=self.registry,
            budget_remaining_usd=self._budget_remaining,
            db_engines=await self._db_engines(),
        )
        driver = get_driver()
        self.outcome.driver_name = driver.name
        can_use_tool = build_can_use_tool(
            self._config.approval_policy,
            self._approvals,
            self._config.databases,
            credential_allows=self._credential_allows,
        )

        # The pump feeds the same queue `emit()` writes to, so an approval
        # request surfaces immediately even though the pump is blocked inside
        # the tool-permission callback that raised it.
        queue = self._events

        async def pump() -> None:
            # Embedding/rerank calls made by this turn's tools (kb_search)
            # report here. Activated inside the pump task, so tool calls the
            # driver makes from here inherit it, and nothing outside the turn
            # does.
            rag_usage.activate(self.rag_usage)
            try:
                async for ev in driver.stream(
                    prompt=self._prompt,
                    spec=spec,
                    policy=self._config.approval_policy,
                    session_id=self._session_id,
                    can_use_tool=can_use_tool,
                    interrupt=self._interrupt,
                ):
                    await queue.put(ev)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await queue.put(ErrorEvent(code="agent_error", message=str(exc)))
            finally:
                await queue.put(_DONE)

        task = asyncio.create_task(pump())
        try:
            while True:
                item = await queue.get()
                if item is _DONE:
                    break
                if item is _FORCE_STOP:
                    # Interrupted, and the driver did not stop by itself in
                    # time. `finally` cancels the pump.
                    break
                event = _preview(cast(AgentEvent, item))
                self._absorb(event)
                yield event

                if self._over_budget():
                    self.outcome.status = RunStatus.aborted
                    self.outcome.error = "conversation budget exceeded"
                    yield ErrorEvent(
                        code="budget_exceeded",
                        message="This conversation has reached its spend limit.",
                    )
                    break
                if self._over_turn_limit() or self._sdk_hit_turn_limit(event):
                    # Surfaced like the budget: a turn cut off by max_turns
                    # used to end silently, with an answer that just stopped.
                    # The platform also counts model calls itself now; before,
                    # the limit was only forwarded to the SDK and trusted.
                    self.outcome.status = RunStatus.aborted
                    self.outcome.error = "maximum agent turns reached"
                    yield ErrorEvent(
                        code="max_turns",
                        message=(
                            "The agent reached its limit of "
                            f"{self._config.models.main.max_turns} model turns for one message."
                        ),
                    )
                    break
        except asyncio.CancelledError:
            self.outcome.status = RunStatus.aborted
            self.outcome.error = "cancelled"
            raise
        finally:
            # The pump owns the driver's generator; cancelling it is what
            # closes the SDK client (and releases any approval wait) when a
            # client disconnects mid-turn.
            if not task.done():
                task.cancel()
            # Wait for that cleanup *shielded*, and with a deadline.
            #
            # Unshielded, this await sits inside Starlette's cancelled anyio
            # scope, which re-cancels this task on every await — and asyncio
            # forwards each of those cancellations to the task being awaited.
            # The pump was re-cancelled straight through its own cleanup, so a
            # pending approval was never expired (reproduced over live HTTP; a
            # one-shot `task.cancel()` in a test cannot show it). Shielding
            # stops the forwarding; the deadline keeps a hung CLI from hanging
            # the request forever.
            with (
                anyio.move_on_after(PUMP_SHUTDOWN_TIMEOUT_S, shield=True),
                contextlib.suppress(asyncio.CancelledError, Exception),
            ):
                await task

    def _absorb(self, ev: AgentEvent) -> None:
        o = self.outcome
        if isinstance(ev, TokenEvent):
            if ev.parent_id:
                self._note_subagent(ev.parent_id, ev.text)
            else:
                o.text += ev.text
        elif isinstance(ev, ThinkingEvent):
            if not ev.parent_id:
                o.thinking += ev.text
        elif isinstance(ev, ToolCallEvent):
            self._absorb_call(ev)
        elif isinstance(ev, ToolResultEvent):
            self._absorb_result(ev)
        elif isinstance(ev, UsageEvent):
            o.tokens_in += ev.tokens_in
            o.tokens_out += ev.tokens_out
            o.cost_usd += ev.cost_usd
            if ev.sdk_session_id:
                o.sdk_session_id = ev.sdk_session_id
            if ev.num_turns is not None:
                o.num_turns = ev.num_turns
            self._model_calls += ev.model_calls
            o.web_searches += ev.web_searches
            if ev.terminal_reason == "max_turns" and not self._interrupt.is_set():
                o.status = RunStatus.aborted
                o.error = "maximum agent turns reached"
        elif isinstance(ev, ErrorEvent) and not self._interrupt.is_set():
            # A driver may report its own interruption as an error; the turn
            # was stopped on purpose, and is recorded as such.
            o.status = RunStatus.error
            o.error = ev.message

    def _absorb_call(self, ev: ToolCallEvent) -> None:
        call: dict[str, Any] = {"id": ev.id, "name": ev.name, "input": ev.input}
        if ev.parent_id:
            call["parent_id"] = ev.parent_id
        self.outcome.tool_calls.append(call)
        self._tool_started[ev.id] = time.perf_counter()
        self.outcome.tool_started_ns[ev.id] = time.time_ns()
        log.info("tool_started", tool=ev.name, call_id=ev.id)

    def _absorb_result(self, ev: ToolResultEvent) -> None:
        o = self.outcome
        for call in o.tool_calls:
            if call["id"] == ev.id:
                call["status"] = ev.status
                call["output"] = ev.output
        started = self._tool_started.pop(ev.id, None)
        if started is not None:
            o.tool_latency_ms[ev.id] = int((time.perf_counter() - started) * 1000)
        log.info(
            "tool_finished",
            call_id=ev.id,
            status=ev.status,
            latency_ms=o.tool_latency_ms.get(ev.id),
            truncated=ev.truncated,
        )

    def _note_subagent(self, parent_id: str, text: str) -> None:
        """A subagent's own text, kept on the delegation call it ran under
        (shown with that call, never as part of the answer)."""
        for call in self.outcome.tool_calls:
            if call["id"] == parent_id:
                notes = str(call.get("subagent_text", "")) + text
                call["subagent_text"] = notes[:SUBAGENT_TEXT_CHARS]
                return

    @property
    def model_calls(self) -> int:
        """Model calls the turn made, as counted for `max_turns`."""
        return self._model_calls

    def resolve_citations(self, text: str) -> list[Citation]:
        """Map the finished answer's ``[n]`` markers back to their chunks.

        Deliberately NOT done inside ``stream()``. Resolution has to happen
        against the exact string that gets persisted, and it must not sit
        between the end of the stream and the commit — a raise there (or a
        client disconnect at an extra yield point) would destroy an answer the
        user has already watched arrive. ``run_message`` calls this after the
        commit instead.
        """
        if self.registry is None:
            return []
        return self.registry.resolve(text)

    def citation_state(self) -> dict | None:
        return self.registry.dump() if self.registry is not None else None

    def _over_turn_limit(self) -> bool:
        limit = self._config.models.main.max_turns
        return limit is not None and self._model_calls > limit

    def _sdk_hit_turn_limit(self, event: AgentEvent) -> bool:
        return (
            isinstance(event, UsageEvent)
            and event.terminal_reason == "max_turns"
            and not self._interrupt.is_set()
        )

    def _over_budget(self) -> bool:
        return (
            self._budget_remaining is not None and self.outcome.cost_usd >= self._budget_remaining
        )


#: What a tool result may carry to the client and into storage (plan §4.1:
#: tool events carry previews). A SQL result or a fetched page used to be
#: streamed and persisted in full, whatever its size.
TOOL_OUTPUT_PREVIEW_CHARS = 8_000


def _preview(event: AgentEvent) -> AgentEvent:
    """Tool events as the client sees them: inputs with credential-shaped
    values redacted, outputs capped. The model already has the originals."""
    if isinstance(event, ToolCallEvent):
        return event.model_copy(update={"input": redact(event.input)})
    if isinstance(event, ToolResultEvent) and len(event.output) > TOOL_OUTPUT_PREVIEW_CHARS:
        kept = event.output[:TOOL_OUTPUT_PREVIEW_CHARS]
        cut = len(event.output) - TOOL_OUTPUT_PREVIEW_CHARS
        return event.model_copy(
            update={"output": f"{kept}\n… ({cut:,} more characters not shown)", "truncated": True}
        )
    return event


__all__ = ["TOOL_OUTPUT_PREVIEW_CHARS", "Turn", "TurnOutcome"]
