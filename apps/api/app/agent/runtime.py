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

from app.agent.approvals import ApprovalRequest, CanUseTool, build_can_use_tool, redact
from app.agent.caps_mcp import McpToolset, build_mcp_toolset
from app.agent.caps_memory import MemoryScope
from app.agent.citations import Citation, CitationRegistry
from app.agent.driver import ClaudeSDKDriver, FakeDriver, get_driver
from app.agent.errors import PROCESS_LEVEL, classify
from app.agent.events import (
    AgentEvent,
    ErrorEvent,
    GuardrailEvent,
    ThinkingEvent,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
    UsageEvent,
)
from app.agent.options import RuntimeSpec, build_runtime_spec
from app.config import settings
from app.datasources.sql_guard import Permissions
from app.db.session import get_sessionmaker
from app.guardrails import turn as turn_guard
from app.guardrails.injection import USER_NOTE, describe, scan
from app.logging import get_logger
from app.models.conversation import RunStatus
from app.models.integration import DbConnection
from app.rag import usage as rag_usage
from app.rag.retrieve import chunks_by_id
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
#: Pause before restarting a turn whose runtime failed to start (task 5.4).
RETRY_BACKOFF_S = 0.5
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
    #: tool call id -> how it was permitted ("auto", "approved", "declined",
    #: "expired", "interrupted", "refused"); stored on its audit row.
    permissions: dict[str, str] = field(default_factory=dict)
    #: Why the main loop stopped, and every model that answered (task 5.4).
    stop_reason: str | None = None
    models: list[str] = field(default_factory=list)
    #: Set when the fallback model answered instead of the main one.
    fallback_model: str | None = None
    #: How the router sorted the message ("simple", "normal", "hard"), when
    #: one is wired in (task 5.10), and what its own call cost.
    route: str | None = None
    route_spend: Any = None


class Turn:
    def __init__(
        self,
        config: AssistantConfig,
        *,
        prompt: str,
        assistant_id: uuid.UUID | None = None,
        session_id: str | None = None,
        budget_remaining_usd: float | None = None,
        budget_message: str | None = None,
        scratch_dir: Path | None = None,
        citation_state: dict | None = None,
        approvals: ApprovalRequest | None = None,
        history: str | None = None,
        memory_scope: MemoryScope | None = None,
    ) -> None:
        self._config = config
        #: What the guardrails found this turn (task 5.3): emitted live, and
        #: saved with the answer.
        self.guard = turn_guard.TurnGuard(
            injection_scan=config.guardrails.injection_scan,
            pii_redaction=config.guardrails.pii_redaction,
            on_finding=lambda f: self.emit(GuardrailEvent(**f)),
        )
        #: The earlier conversation, when this turn starts a fresh session
        #: instead of resuming one (task 5.2).
        self._history = history
        #: Whose memory files the memory tool reads and writes (task 5.2).
        self._memory_scope = memory_scope
        self._prompt = prompt
        self._assistant_id = assistant_id
        self._session_id = session_id
        self._budget_remaining = budget_remaining_usd
        #: What to say when the spend runs out: the conversation's cap by
        #: default, or the org's or assistant's budget when that is tighter.
        self._budget_message = budget_message
        self._scratch_dir = scratch_dir
        self._approvals = approvals
        self.outcome = TurnOutcome()
        #: When the turn began, for each tool call's place on the run's
        #: timeline (task 5.9). Reset when `stream()` starts.
        self._t0 = time.perf_counter()
        #: Error codes already sent to the client this turn.
        self._error_codes: set[str] = set()
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
        self._t0 = time.perf_counter()
        # Tools from the assistant's MCP servers (task 4.6). Their
        # connections open on first use and are closed when the turn ends.
        mcp = await build_mcp_toolset(self._config, self._assistant_id)
        spec = build_runtime_spec(
            self._config,
            assistant_id=self._assistant_id,
            scratch_dir=self._scratch_dir,
            citations=self.registry,
            budget_remaining_usd=self._budget_remaining,
            db_engines=await self._db_engines(),
            mcp_tools=mcp.tools,
            history=self._history,
            memory_scope=self._memory_scope,
        )
        spec.guard = self.guard
        spec.over_budget = self._over_budget
        prompt = self._guarded_prompt()
        driver = get_driver()
        self.outcome.driver_name = driver.name
        # The pump feeds the same queue `emit()` writes to, so an approval
        # request surfaces immediately even though the pump is blocked inside
        # the tool-permission callback that raised it.
        queue = self._events
        task = asyncio.create_task(self._pump(driver, spec, prompt, self._permission_callback(mcp)))
        try:
            while True:
                item = await queue.get()
                if item is _DONE:
                    break
                if item is _FORCE_STOP:
                    # Interrupted, and the driver did not stop by itself in
                    # time. `finally` cancels the pump.
                    break
                event = self._annotate(_preview(cast(AgentEvent, item)))
                if isinstance(event, ErrorEvent):
                    # The CLI can report one failure twice: on the message,
                    # then as the exception that ends the run.
                    if event.code in self._error_codes:
                        continue
                    self._error_codes.add(event.code)
                self._absorb(event)
                yield event

                if self._over_budget():
                    self.outcome.status = RunStatus.aborted
                    self.outcome.error = (
                        "budget exceeded"
                        if self._budget_message
                        else "conversation budget exceeded"
                    )
                    yield ErrorEvent(
                        code="budget_exceeded",
                        message=self._budget_message
                        or "This conversation has reached its spend limit.",
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
            refused = self._settle_model_outcome(spec.fallback_model)
            if refused is not None:
                yield refused
        except asyncio.CancelledError:
            self.outcome.status = RunStatus.aborted
            self.outcome.error = "cancelled"
            raise
        finally:
            await self._shut_down(task, mcp)

    async def _shut_down(self, task: asyncio.Task[None], mcp: McpToolset) -> None:
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
        # After the driver has stopped calling tools: close this turn's
        # MCP connections (and so stop any local-command servers).
        with (
            anyio.move_on_after(PUMP_SHUTDOWN_TIMEOUT_S, shield=True),
            contextlib.suppress(Exception),
        ):
            await mcp.aclose()

    def _permission_callback(self, mcp: McpToolset) -> CanUseTool:
        return build_can_use_tool(
            self._config.approval_policy,
            self._approvals,
            self._config.databases,
            credential_allows=self._credential_allows,
            http=self._config.tools.http_request,
            read_only_mcp=frozenset(t.qualified_name for t in mcp.tools if t.read_only),
            mcp_modes=mcp.modes,
            permissions=self.outcome.permissions,
        )

    async def _pump(
        self,
        driver: FakeDriver | ClaudeSDKDriver,
        spec: RuntimeSpec,
        prompt: str,
        can_use_tool: CanUseTool,
    ) -> None:
        """Run the driver into the turn's queue, restarting it when it failed
        before saying anything (task 5.4), then mark the end."""
        # Embedding/rerank calls made by this turn's tools (kb_search) report
        # here. Activated inside the pump task, so tool calls the driver makes
        # from here inherit it, and nothing outside the turn does.
        rag_usage.activate(self.rag_usage)
        # And the guardrails, for the post-tool step under every tool.
        turn_guard.activate(self.guard)
        try:
            for attempt in range(settings.agent_turn_retries + 1):
                last = attempt == settings.agent_turn_retries
                if not await self._run_driver(driver, spec, prompt, can_use_tool, last=last):
                    break
                log.warning("turn_retry", attempt=attempt + 1)
                await asyncio.sleep(RETRY_BACKOFF_S * (attempt + 1))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failed = classify(exc)
            log.exception("turn_driver_failed", code=failed.code)
            await self._events.put(
                ErrorEvent(code=failed.code, message=failed.message, retryable=failed.retryable)
            )
        finally:
            await self._events.put(_DONE)

    def _settle_model_outcome(self, fallback: str | None) -> ErrorEvent | None:
        """Who answered, and whether anyone did (task 5.4). A refusal is not
        an error: the model worked and declined. It ends the run as
        `refused` with a notice that says so, and whether the fallback model
        was asked too."""
        o = self.outcome
        if fallback is not None and fallback in o.models:
            o.fallback_model = fallback
        if o.stop_reason != "refusal":
            return None
        o.status = RunStatus.refused
        o.error = "the model declined to answer"
        also = f" The fallback model ({fallback}) declined too." if o.fallback_model else ""
        return ErrorEvent(
            code="refused",
            message=f"The model declined to answer this.{also} Rephrasing it may help.",
        )

    async def _run_driver(
        self,
        driver: FakeDriver | ClaudeSDKDriver,
        spec: RuntimeSpec,
        prompt: str,
        can_use_tool: CanUseTool,
        *,
        last: bool,
    ) -> bool:
        """One attempt at the turn. True means "start it again": the runtime
        failed at the process level (it didn't start, or died) before a
        single event reached the client, so nothing was said or done twice
        (task 5.4, `errors.PROCESS_LEVEL`). API-level failures are not
        retried here: the CLI has already retried those."""
        said = False
        stream = driver.stream(
            prompt=prompt,
            spec=spec,
            policy=self._config.approval_policy,
            session_id=self._session_id,
            can_use_tool=can_use_tool,
            interrupt=self._interrupt,
        )
        # Both drivers are async generators; closed here on every exit.
        async with contextlib.aclosing(cast(AsyncGenerator[AgentEvent, None], stream)) as events:
            async for ev in events:
                if (
                    not said
                    and not last
                    and isinstance(ev, ErrorEvent)
                    and ev.code in PROCESS_LEVEL
                ):
                    return True
                said = True
                await self._events.put(ev)
        return False

    def _guarded_prompt(self) -> str:
        """The input guardrail (UserPromptSubmit's job, task 5.3): a message
        trying to override or extract the assistant's instructions still
        goes through, with a note that the rules still apply. Applied here,
        before either driver, so the offline one gets it too; the stored
        message stays exactly what the user typed."""
        if not self.guard.injection_scan:
            return self._prompt
        signals = scan(self._prompt)
        if not signals:
            return self._prompt
        self.guard.record(
            "injection",
            "user_message",
            f"The message tried to change or reveal the assistant's instructions "
            f"({describe(signals)}); it was answered under the assistant's normal rules.",
        )
        return USER_NOTE.format(signals=describe(signals)) + "\n\n" + self._prompt

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
            self._absorb_usage(ev)
        elif isinstance(ev, ErrorEvent) and not self._interrupt.is_set():
            # A driver may report its own interruption as an error; the turn
            # was stopped on purpose, and is recorded as such.
            o.status = RunStatus.error
            o.error = ev.message

    def _absorb_usage(self, ev: UsageEvent) -> None:
        o = self.outcome
        o.tokens_in += ev.tokens_in
        o.tokens_out += ev.tokens_out
        o.cost_usd += ev.cost_usd
        if ev.sdk_session_id:
            o.sdk_session_id = ev.sdk_session_id
        if ev.num_turns is not None:
            o.num_turns = ev.num_turns
        self._model_calls += ev.model_calls
        o.web_searches += ev.web_searches
        # Why the main loop stopped, and who answered (task 5.4).
        if ev.stop_reason:
            o.stop_reason = ev.stop_reason
        o.models.extend(m for m in ev.models if m not in o.models)
        if ev.terminal_reason == "max_turns" and not self._interrupt.is_set():
            o.status = RunStatus.aborted
            o.error = "maximum agent turns reached"

    def _absorb_call(self, ev: ToolCallEvent) -> None:
        call: dict[str, Any] = {"id": ev.id, "name": ev.name, "input": ev.input}
        # Saved with the call, so the run's trace can say when it ran.
        call["started_ms"] = int((time.perf_counter() - self._t0) * 1000)
        if ev.parent_id:
            call["parent_id"] = ev.parent_id
        self.outcome.tool_calls.append(call)
        self._tool_started[ev.id] = time.perf_counter()
        self.outcome.tool_started_ns[ev.id] = time.time_ns()
        log.info("tool_started", tool=ev.name, call_id=ev.id)

    def _annotate(self, ev: AgentEvent) -> AgentEvent:
        """A tool result carries how its call was permitted (task 4.7), so the
        client can show it without waiting for the stored message."""
        if isinstance(ev, ToolResultEvent) and ev.id in self.outcome.permissions:
            return ev.model_copy(update={"permission": self.outcome.permissions[ev.id]})
        return ev

    def _absorb_result(self, ev: ToolResultEvent) -> None:
        o = self.outcome
        for call in o.tool_calls:
            if call["id"] == ev.id:
                call["status"] = ev.status
                call["output"] = ev.output
                if ev.id in o.permissions:
                    call["permission"] = o.permissions[ev.id]
        started = self._tool_started.pop(ev.id, None)
        if started is not None:
            o.tool_latency_ms[ev.id] = int((time.perf_counter() - started) * 1000)
            for call in o.tool_calls:
                if call["id"] == ev.id:
                    call["duration_ms"] = o.tool_latency_ms[ev.id]
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

    async def recall_citations(self, assistant_id: uuid.UUID, text: str) -> None:
        """Before `resolve_citations`: load the chunks behind markers the
        answer reuses from an earlier turn (answered from passages already in
        the resumed context, with no new search), so they resolve too instead
        of showing as dead ``[n]`` text. See `CitationRegistry.carried_over`.

        Its own short-lived session, like the kb tools': a failure here must
        never touch the transaction that saves the answer."""
        if self.registry is None:
            return
        carried = self.registry.carried_over(text)
        if not carried:
            return
        async with get_sessionmaker()() as session:
            chunks = await chunks_by_id(
                session, assistant_id=assistant_id, chunk_ids=list(carried.values())
            )
        self.registry.recall(chunks)

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
