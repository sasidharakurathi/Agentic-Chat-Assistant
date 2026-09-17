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
import re
import uuid
from collections.abc import AsyncIterator

from app.agent.approvals import build_can_use_tool
from app.agent.caps import ALL_CAPS
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
from app.agent.options import RuntimeSpec, build_claude_options
from app.config import settings
from app.logging import get_logger
from app.schemas.assistant_config import ApprovalPolicy

log = get_logger(__name__)


def _estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    rate_in, rate_out = PRICE_PER_MTOK.get(model, (0.0, 0.0))
    return round(rate_in * tokens_in / 1_000_000 + rate_out * tokens_out / 1_000_000, 6)


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
    ) -> AsyncIterator[AgentEvent]:
        calc_allowed = "mcp__caps__calculator" in spec.allowed_tools
        expr_match = re.search(r"(-?\d[\d\s.+\-*/%()]*\d|\d)", prompt)

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
            result = await ALL_CAPS["calculator"].handler({"expression": expr})
            text = result["content"][0]["text"]
            yield ToolResultEvent(id="fake-1", status="success", output=text)
            reply_parts = [text]
        else:
            reply_parts = [
                "(fake driver)",
                "you",
                "said:",
                prompt.strip() or "(nothing)",
            ]

        for part in reply_parts:
            await asyncio.sleep(0)  # let the event loop breathe / interleave
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


class ClaudeSDKDriver:
    """Runs the real Agent SDK loop. Unverified until an API key + CLI are present."""

    name = "claude"

    async def stream(
        self,
        *,
        prompt: str,
        spec: RuntimeSpec,
        policy: ApprovalPolicy,
        session_id: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeSDKClient,
            ResultMessage,
            SystemMessage,
            TextBlock,
            ThinkingBlock,
            ToolResultBlock,
            ToolUseBlock,
        )

        options = build_claude_options(spec, build_can_use_tool(policy))
        if session_id:
            options.resume = session_id

        try:
            async with ClaudeSDKClient(options=options) as client:
                await client.query(prompt)
                async for message in client.receive_response():
                    if isinstance(message, SystemMessage):
                        continue
                    if isinstance(message, AssistantMessage):
                        for block in message.content:
                            if isinstance(block, TextBlock):
                                yield TokenEvent(text=block.text)
                            elif isinstance(block, ThinkingBlock):
                                yield ThinkingEvent(text=block.thinking)
                            elif isinstance(block, ToolUseBlock):
                                yield ToolCallEvent(
                                    id=block.id, name=block.name, input=dict(block.input or {})
                                )
                            elif isinstance(block, ToolResultBlock):
                                yield ToolResultEvent(
                                    id=block.tool_use_id,
                                    status="error" if block.is_error else "success",
                                    output=_block_text(block.content),
                                )
                    elif isinstance(message, ResultMessage):
                        usage = message.usage or {}
                        yield UsageEvent(
                            tokens_in=int(usage.get("input_tokens", 0)),
                            tokens_out=int(usage.get("output_tokens", 0)),
                            cost_usd=float(getattr(message, "total_cost_usd", 0.0) or 0.0),
                            sdk_session_id=message.session_id,
                            num_turns=message.num_turns,
                            terminal_reason=message.terminal_reason,
                        )
        except Exception as exc:
            log.exception("claude_driver_failed")
            yield ErrorEvent(code="agent_error", message=str(exc))


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
