"""Agent drivers: something that turns a prompt + spec into a stream of events.

- ``ClaudeSDKDriver`` runs the real Anthropic Agent SDK loop (needs the Claude
  Code CLI + ``ANTHROPIC_API_KEY``).
- ``FakeDriver`` is deterministic and offline — used in tests and when no API key
  is configured, so the whole chat path is exercisable without spending tokens.

Drivers emit every event kind **except** ``done`` — the chat service adds that
after it has persisted the message + run rows.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from app.agent.approvals import CanUseTool, build_can_use_tool
from app.agent.caps import ALL_CAPS, CapabilityTool
from app.agent.events import (
    AgentEvent,
    ErrorEvent,
    ThinkingEvent,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
    UsageEvent,
)
from app.agent.models import PRICE_PER_MTOK
from app.agent.options import SUBAGENT_TOOL, RuntimeSpec, build_claude_options
from app.agent.post_tool import run_capability
from app.config import settings
from app.logging import get_logger
from app.schemas.assistant_config import ApprovalPolicy

log = get_logger(__name__)


def _estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    rate_in, rate_out = PRICE_PER_MTOK.get(model, (0.0, 0.0))
    return round(rate_in * tokens_in / 1_000_000 + rate_out * tokens_out / 1_000_000, 6)


def _cap_result(result: dict[str, Any]) -> tuple[str, str]:
    """(text, status) from a capability's return value.

    Caps flag refusals with `is_error` (the SDK's convention, see
    `caps._err`). The fake driver has to honour it for the same reason the
    real one does: a blocked query rendered as a successful tool call tells
    the user their statement ran.
    """
    text = str(result["content"][0]["text"])
    return text, ("error" if result.get("is_error") else "success")


_SEARCH_TRIGGER = re.compile(r"^\s*search(?:\s+twice)?\s*:\s*", re.IGNORECASE)


def _search_query(prompt: str) -> str:
    """The query a real model would send: the question, without the
    "search:" / "search twice:" trigger. Sending the whole prompt made the
    trigger words part of the query, and the reranker scored passages
    against "search twice: …" low enough to drop them all."""
    return _SEARCH_TRIGGER.sub("", prompt).strip() or prompt


async def _fake_search(
    prompt: str, kb_search_tool: Any, reply: list[str]
) -> AsyncIterator[AgentEvent]:
    """One kb_search call, or two when the prompt says "search twice". Two
    calls in one turn are the only way to exercise citation markers
    continuing across calls ([1][2] then [3][4]) rather than restarting — the
    property the whole registry exists for — without a real model."""
    query = _search_query(prompt)
    queries = [query, query + " (follow-up)"] if "twice" in prompt.lower() else [query]
    for n, query in enumerate(queries, start=1):
        call_id = f"fake-{n}"
        yield ToolCallEvent(id=call_id, name="mcp__caps__kb_search", input={"query": query})
        result = await run_capability(kb_search_tool.handler, {"query": query})
        text, status = _cap_result(result)
        yield ToolResultEvent(id=call_id, status=status, output=text)
        reply.append(text)


async def _fake_delegation(
    prompt: str, kb_search_tool: Any, reply: list[str]
) -> AsyncIterator[AgentEvent]:
    """Delegate to the retrieval subagent the way the SDK does: one `Agent`
    call, with the subagent's own notes and kb_search calls nested under it
    (`parent_id`), and its findings as that call's result. Only the main
    agent's text is the answer; the findings go into `reply`."""
    task = "fake-task-1"
    query = _search_query(prompt)
    yield ToolCallEvent(
        id=task, name=SUBAGENT_TOOL, input={"subagent_type": "retrieval", "prompt": prompt}
    )
    yield TokenEvent(text="Searching the knowledge base. ", parent_id=task)
    yield ToolCallEvent(
        id="fake-sub-1", name="mcp__caps__kb_search", input={"query": query}, parent_id=task
    )
    result = await run_capability(kb_search_tool.handler, {"query": query})
    text, status = _cap_result(result)
    yield ToolResultEvent(id="fake-sub-1", status=status, output=text, parent_id=task)
    yield TokenEvent(text="Kept the passages that bear on it.", parent_id=task)
    yield ToolResultEvent(id=task, status=status, output=text)
    reply.append(text)


def _mcp_call(prompt: str, spec: RuntimeSpec) -> tuple[CapabilityTool, dict[str, Any]] | None:
    """`mcp: github.search {"q": "x"}` -> that tool and its input, when this
    turn offers it."""
    lowered = prompt.lower()
    if "mcp:" not in lowered:
        return None
    rest = prompt[lowered.index("mcp:") + 4 :].strip()
    target, _, raw = rest.partition(" ")
    server, _, tool = target.partition(".")
    cap = next((t for t in spec.caps_tools if t.server == server and t.name == tool), None)
    if cap is None:
        return None
    try:
        args = json.loads(raw) if raw.strip() else {}
    except ValueError:
        args = {}
    return cap, args if isinstance(args, dict) else {}


def _permitted_call(prompt: str, spec: RuntimeSpec) -> tuple[CapabilityTool, dict[str, Any]] | None:
    """The permission-checked call a prompt asks for, if this turn offers it."""
    lowered = prompt.lower()
    tools = {t.qualified_name: t for t in spec.caps_tools}
    mcp = _mcp_call(prompt, spec)
    if mcp is not None:
        return mcp
    http = tools.get("mcp__caps__http_request")
    if http is not None and "http:" in lowered:
        return http, _http_args(prompt)
    sql = tools.get("mcp__caps__sql_query")
    if sql is not None and "sql:" in lowered:
        statement = prompt[lowered.index("sql:") + 4 :].strip()
        return sql, {"connection_id": spec.sql_connection_hint or "", "sql": statement}
    return None


def _http_args(prompt: str) -> dict[str, Any]:
    """`http: POST https://x.test/api {"a": 1}` -> the tool's input."""
    rest = prompt[prompt.lower().index("http:") + 5 :].strip().split(None, 2)
    method = "GET"
    if rest and rest[0].upper() in ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
        method = rest.pop(0).upper()
    args: dict[str, Any] = {"method": method, "url": rest[0] if rest else ""}
    if len(rest) > 1:
        args["body"] = " ".join(rest[1:])
    return args


async def _fake_permitted_call(
    name: str,
    args: dict[str, Any],
    cap: CapabilityTool,
    can_use_tool: CanUseTool | None,
    reply: list[str],
) -> AsyncIterator[AgentEvent]:
    """One tool call that goes through `can_use_tool` first, as the SDK does:
    a denial becomes a `denied` result, and `updated_input` is what runs."""
    yield ToolCallEvent(id="fake-1", name=name, input=args)
    if can_use_tool is not None:
        # The SDK passes the call's id in the context; so does this, so the
        # permission lands on the right audit row.
        ctx = SimpleNamespace(tool_use_id="fake-1")
        decision = await can_use_tool(name, args, ctx)  # type: ignore[arg-type]
        if type(decision).__name__ != "PermissionResultAllow":
            message = getattr(decision, "message", "") or "not permitted"
            yield ToolResultEvent(id="fake-1", status="denied", output=message)
            reply.append(f"I couldn't run that: {message}")
            return
        args = getattr(decision, "updated_input", None) or args
    result = await run_capability(cap.handler, args)
    text, status = _cap_result(result)
    yield ToolResultEvent(id="fake-1", status=status, output=text)
    reply.append(text)


class FakeDriver:
    """Offline, deterministic. Answers arithmetic via the real calculator cap;
    otherwise echoes the prompt word-by-word."""

    name = "fake"

    async def stream(
        self,
        *,
        prompt: str,
        spec: RuntimeSpec,
        policy: ApprovalPolicy,
        session_id: str | None = None,
        can_use_tool: CanUseTool | None = None,
        interrupt: asyncio.Event | None = None,
    ) -> AsyncIterator[AgentEvent]:
        calc_allowed = "mcp__caps__calculator" in spec.enabled_tools
        expr_match = re.search(r"(-?\d[\d\s.+\-*/%()]*\d|\d)", prompt)
        kb_search_tool = next((t for t in spec.caps_tools if t.name == "kb_search"), None)
        permitted = _permitted_call(prompt, spec)

        reply_parts: list[str]
        if (
            calc_allowed
            and ("calcul" in prompt.lower() or "+" in prompt or "*" in prompt)
            and expr_match
        ):
            expr = expr_match.group(0).strip()
            yield ToolCallEvent(
                id="fake-1", name="mcp__caps__calculator", input={"expression": expr}
            )
            result = await run_capability(ALL_CAPS["calculator"].handler, {"expression": expr})
            text, status = _cap_result(result)
            yield ToolResultEvent(id="fake-1", status=status, output=text)
            reply_parts = [text]
        elif (
            kb_search_tool is not None
            and "search" in prompt.lower()
            and any(s.name == "retrieval" for s in spec.subagents)
        ):
            reply_parts = []
            async for ev in _fake_delegation(prompt, kb_search_tool, reply_parts):
                yield ev
        elif kb_search_tool is not None and "search" in prompt.lower():
            reply_parts = []
            async for ev in _fake_search(prompt, kb_search_tool, reply_parts):
                yield ev
        elif permitted is not None:
            # A tool that goes through the real permission callback, as the
            # SDK's calls do: "mcp: <server>.<tool> {json}", "http: [METHOD]
            # <url> [body]" or "sql: <statement>". This is what makes the
            # approval flow exercisable without spending credits.
            cap, args = permitted
            reply_parts = []
            async for ev in _fake_permitted_call(
                cap.qualified_name, args, cap, can_use_tool, reply_parts
            ):
                yield ev
        else:
            reply_parts = [
                "(fake driver)",
                "you",
                "said:",
                prompt.strip() or "(nothing)",
            ]

        for part in reply_parts:
            await asyncio.sleep(0)  # let the event loop breathe / interleave
            if interrupt is not None and interrupt.is_set():
                break  # stop generating; still report what was used, below
            yield TokenEvent(text=part + " ")

        answer = " ".join(reply_parts)
        tokens_in = max(1, len(prompt) // 4)
        tokens_out = max(1, len(answer) // 4)
        # Echo the resumed session back unchanged, or mint a fresh one — mirrors
        # how the real SDK either continues or starts a session, so resume
        # wiring (chat.py <-> session_store) is exercisable offline.
        yield UsageEvent(
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=_estimate_cost(spec.model, tokens_in, tokens_out),
            sdk_session_id=session_id or f"fake-{uuid.uuid4().hex[:12]}",
        )


#: Builds the SDK's transport from the finished options. None (production)
#: lets the SDK start the bundled CLI. Tests put a scripted CLI here, so the
#: real client, its message parser and its control protocol (permission
#: requests, hook callbacks, in-process MCP calls) all run without one.
TransportFactory = Callable[[Any], Any]
transport_factory: TransportFactory | None = None


class ClaudeSDKDriver:
    """Runs the real Agent SDK loop against the bundled CLI (or, in tests, a
    scripted transport: see `transport_factory`)."""

    name = "claude"

    async def stream(
        self,
        *,
        prompt: str,
        spec: RuntimeSpec,
        policy: ApprovalPolicy,
        session_id: str | None = None,
        can_use_tool: CanUseTool | None = None,
        interrupt: asyncio.Event | None = None,
    ) -> AsyncIterator[AgentEvent]:
        from claude_agent_sdk import ClaudeSDKClient, PermissionResultDeny

        gate = can_use_tool or build_can_use_tool(policy)
        # Which tool calls the permission router refused, by tool_use_id, so
        # their results can be labelled "denied" rather than a generic error —
        # the chat UI shows a reviewer's decision differently from a failure.
        denied: set[str] = set()

        async def recording_gate(name: str, tool_input: dict[str, Any], ctx: Any) -> Any:
            decision = await gate(name, tool_input, ctx)
            if isinstance(decision, PermissionResultDeny) and getattr(ctx, "tool_use_id", None):
                denied.add(ctx.tool_use_id)
            return decision

        options = build_claude_options(spec, recording_gate)
        if session_id:
            options.resume = session_id

        ledger = _UsageLedger()
        partial = _PartialText()
        try:
            transport = transport_factory(options) if transport_factory is not None else None
            async with ClaudeSDKClient(options=options, transport=transport) as client:
                await client.query(prompt)
                relay = (
                    asyncio.create_task(_relay_interrupt(interrupt, client))
                    if interrupt is not None
                    else None
                )
                try:
                    async for message in client.receive_response():
                        for event in _events_for(message, denied, ledger, partial=partial):
                            yield event
                finally:
                    if relay is not None:
                        relay.cancel()
        except Exception as exc:
            log.exception("claude_driver_failed")
            yield ErrorEvent(code="agent_error", message=str(exc))


@dataclass
class _UsageLedger:
    """Spend observed so far in one query.

    Cost used to arrive only in the terminal `ResultMessage`. A turn cut short
    by a disconnect never gets one, so it was recorded as free even though
    every model call it made was billed — and the mid-stream budget check
    could never fire, because there was no cost to check until the end.

    Each completed `AssistantMessage` carries its own `usage`, so spend is
    emitted as it is incurred (priced from `PRICE_PER_MTOK`), and the result
    message then *settles* to the SDK's authoritative totals. The sum of
    everything emitted equals what the SDK reports, whether or not the turn
    finished.
    """

    #: message_id -> (input, output) already counted. The CLI can emit
    #: several AssistantMessages for one API response, repeating its usage.
    seen: dict[str, tuple[int, int]] = field(default_factory=dict)
    #: message_ids whose cache tokens have already been priced in.
    cache_billed: set[str] = field(default_factory=set)
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0

    def observe(
        self,
        message_id: str | None,
        model: str,
        usage: dict[str, Any] | None,
        *,
        main: bool = True,
    ) -> UsageEvent | None:
        """`main`: whether the call was the main agent's. A subagent's calls
        cost money like any other, but are not turns of the main agent, so
        they don't count towards its `max_turns`."""
        if not usage or not message_id:
            return None  # nothing to dedupe on; the settle step still covers it
        tin, tout = int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)
        # Cache tokens are priced at the input rate here — an overestimate for
        # cache reads, on purpose: a budget should trip early, not late. The
        # settle step corrects the total to the SDK's real figure.
        cached = int(usage.get("cache_creation_input_tokens") or 0) + int(
            usage.get("cache_read_input_tokens") or 0
        )
        new_call = message_id not in self.seen
        prev_in, prev_out = self.seen.get(message_id, (0, 0))
        d_in, d_out = max(0, tin - prev_in), max(0, tout - prev_out)
        if not (d_in or d_out):
            return None
        self.seen[message_id] = (max(tin, prev_in), max(tout, prev_out))
        cached_share = 0
        if cached and message_id not in self.cache_billed:
            cached_share = cached
            self.cache_billed.add(message_id)
        cost = _estimate_cost(model, d_in + cached_share, d_out)
        self.tokens_in += d_in
        self.tokens_out += d_out
        self.cost_usd += cost
        return UsageEvent(
            tokens_in=d_in,
            tokens_out=d_out,
            cost_usd=cost,
            model_calls=1 if new_call and main else 0,
        )

    def settle(self, message: Any) -> UsageEvent:
        usage = message.usage or {}
        total_cost = float(getattr(message, "total_cost_usd", 0.0) or 0.0)
        if total_cost <= 0 and self.cost_usd > 0:
            # The SDK reports 0 for a model it has no price for. Settling to
            # that would silently zero out spend that genuinely happened, so
            # our own estimate stands (observed live: 40 tokens, "$0").
            total_cost = self.cost_usd
        return UsageEvent(
            # Tokens never go below what was already reported; cost may be
            # corrected downwards (see the cache-token note above).
            tokens_in=max(0, int(usage.get("input_tokens", 0)) - self.tokens_in),
            tokens_out=max(0, int(usage.get("output_tokens", 0)) - self.tokens_out),
            cost_usd=round(total_cost - self.cost_usd, 6),
            sdk_session_id=message.session_id,
            num_turns=message.num_turns,
            terminal_reason=message.terminal_reason,
            web_searches=int((usage.get("server_tool_use") or {}).get("web_search_requests") or 0),
        )


@dataclass
class _PartialText:
    """Streams text as it is generated, without then repeating it.

    With `include_partial_messages` the SDK sends every delta as a
    `StreamEvent`, and *then* the finished `AssistantMessage` with the same
    text again. This driver used to read only the finished message, so on the
    real SDK a whole answer arrived as one token at the very end — no
    streaming at all. Now deltas become tokens, and a finished message whose
    text already streamed is skipped. Anything that never streamed (a
    subagent's message without partials) is still emitted from its blocks,
    as before.
    """

    #: parent_tool_use_id -> the message id currently streaming under it. A
    #: subagent's deltas carry its own parent, so its message in flight is
    #: never mistaken for the main agent's.
    current: dict[str | None, str] = field(default_factory=dict)
    streamed_text: set[str] = field(default_factory=set)
    streamed_thinking: set[str] = field(default_factory=set)

    def feed(self, raw: dict[str, Any], parent: str | None) -> list[AgentEvent]:
        kind = raw.get("type")
        if kind == "message_start":
            message_id = (raw.get("message") or {}).get("id")
            if message_id:
                self.current[parent] = str(message_id)
            return []
        if kind != "content_block_delta":
            return []
        delta = raw.get("delta") or {}
        message_id = self.current.get(parent)
        if delta.get("type") == "text_delta" and delta.get("text"):
            if message_id:
                self.streamed_text.add(message_id)
            return [TokenEvent(text=str(delta["text"]), parent_id=parent)]
        if delta.get("type") == "thinking_delta" and delta.get("thinking"):
            if message_id:
                self.streamed_thinking.add(message_id)
            return [ThinkingEvent(text=str(delta["thinking"]), parent_id=parent)]
        return []


async def _relay_interrupt(interrupt: asyncio.Event, client: Any) -> None:
    """Turn a user's "stop" into the SDK's own interrupt. The CLI then ends
    the turn cleanly and still sends its result message, so the usage it
    reports is the real figure rather than the in-flight estimate."""
    await interrupt.wait()
    try:
        await client.interrupt()
    except Exception as exc:  # the runtime's grace deadline is the backstop
        log.warning("sdk_interrupt_failed", error=str(exc)[:200])


def _events_for(
    message: object,
    denied: set[str],
    ledger: _UsageLedger | None = None,
    *,
    partial: _PartialText | None = None,
) -> list[AgentEvent]:
    """Map one SDK message to the events it produces. Pure, so it is testable
    against the SDK's own dataclasses without a CLI.

    Tool *results* arrive in a `UserMessage`, not an `AssistantMessage`: in
    the protocol the model calls a tool and the "user" side answers with its
    result. This used to inspect only `AssistantMessage`, so on the real
    driver no `tool_result` event was ever emitted — tool cards spun forever,
    and persisted turns held calls with no results. `FakeDriver` masked it.
    """
    from claude_agent_sdk import (
        AssistantMessage,
        ResultMessage,
        StreamEvent,
        TextBlock,
        ThinkingBlock,
        ToolResultBlock,
        ToolUseBlock,
        UserMessage,
    )

    ledger = ledger if ledger is not None else _UsageLedger()
    partial = partial if partial is not None else _PartialText()
    events: list[AgentEvent] = []
    if isinstance(message, StreamEvent):
        return partial.feed(message.event or {}, message.parent_tool_use_id)
    if isinstance(message, AssistantMessage):
        # A subagent's messages carry the id of the delegation call they run
        # under. It used to be ignored, so with `forward_subagent_text` the
        # subagent's working notes streamed, and were saved, as part of the
        # main answer.
        parent = message.parent_tool_use_id
        text_streamed = message.message_id in partial.streamed_text
        thinking_streamed = message.message_id in partial.streamed_thinking
        for block in message.content:
            if isinstance(block, TextBlock):
                if not text_streamed:
                    events.append(TokenEvent(text=block.text, parent_id=parent))
            elif isinstance(block, ThinkingBlock):
                if not thinking_streamed:
                    events.append(ThinkingEvent(text=block.thinking, parent_id=parent))
            elif isinstance(block, ToolUseBlock):
                events.append(
                    ToolCallEvent(
                        id=block.id,
                        name=block.name,
                        input=dict(block.input or {}),
                        parent_id=parent,
                    )
                )
        spend = ledger.observe(
            message.message_id, message.model, message.usage, main=parent is None
        )
        if spend is not None:
            events.append(spend)
    elif isinstance(message, UserMessage) and isinstance(message.content, list):
        events.extend(
            _tool_result(block, denied, message.parent_tool_use_id)
            for block in message.content
            if isinstance(block, ToolResultBlock)
        )
    elif isinstance(message, ResultMessage):
        events.append(ledger.settle(message))
    return events


def _tool_result(block: Any, denied: set[str], parent: str | None = None) -> ToolResultEvent:
    if block.tool_use_id in denied:
        status = "denied"
    elif block.is_error:
        status = "error"
    else:
        status = "success"
    return ToolResultEvent(
        id=block.tool_use_id, status=status, output=_block_text(block.content), parent_id=parent
    )


def _block_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return str(content)


def get_driver() -> FakeDriver | ClaudeSDKDriver:
    choice = settings.agent_driver
    if choice == "fake":
        return FakeDriver()
    if choice == "claude":
        return ClaudeSDKDriver()
    # auto
    if settings.app_env == "test" or not settings.anthropic_api_key:
        return FakeDriver()
    return ClaudeSDKDriver()


__all__ = ["ClaudeSDKDriver", "FakeDriver", "get_driver"]
