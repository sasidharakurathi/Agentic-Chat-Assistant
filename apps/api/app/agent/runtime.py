"""One agent turn: pick a driver, stream events, accumulate the outcome, and
enforce the per-conversation budget mid-stream.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

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
from app.models.conversation import RunStatus
from app.schemas.assistant_config import AssistantConfig


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


class Turn:
    def __init__(
        self,
        config: AssistantConfig,
        *,
        prompt: str,
        session_id: str | None = None,
        budget_remaining_usd: float | None = None,
        scratch_dir: Path | None = None,
    ) -> None:
        self._config = config
        self._prompt = prompt
        self._session_id = session_id
        self._budget_remaining = budget_remaining_usd
        self._scratch_dir = scratch_dir
        self.outcome = TurnOutcome()

    async def stream(self) -> AsyncIterator[AgentEvent]:
        spec = build_runtime_spec(self._config, scratch_dir=self._scratch_dir)
        driver = get_driver()
        self.outcome.driver_name = driver.name
        try:
            async for ev in driver.stream(
                prompt=self._prompt,
                spec=spec,
                policy=self._config.approval_policy,
                session_id=self._session_id,
            ):
                self._absorb(ev)
                yield ev
                if self._over_budget():
                    self.outcome.status = RunStatus.aborted
                    self.outcome.error = "conversation budget exceeded"
                    yield ErrorEvent(
                        code="budget_exceeded",
                        message="This conversation has reached its spend limit.",
                    )
                    return
        except asyncio.CancelledError:
            self.outcome.status = RunStatus.aborted
            self.outcome.error = "cancelled"
            raise

    def _absorb(self, ev: AgentEvent) -> None:
        o = self.outcome
        if isinstance(ev, TokenEvent):
            o.text += ev.text
        elif isinstance(ev, ThinkingEvent):
            o.thinking += ev.text
        elif isinstance(ev, ToolCallEvent):
            o.tool_calls.append({"id": ev.id, "name": ev.name, "input": ev.input})
        elif isinstance(ev, ToolResultEvent):
            for call in o.tool_calls:
                if call["id"] == ev.id:
                    call["status"] = ev.status
                    call["output"] = ev.output
        elif isinstance(ev, UsageEvent):
            o.tokens_in += ev.tokens_in
            o.tokens_out += ev.tokens_out
            o.cost_usd += ev.cost_usd
            if ev.sdk_session_id:
                o.sdk_session_id = ev.sdk_session_id
            if ev.num_turns is not None:
                o.num_turns = ev.num_turns
            if ev.terminal_reason == "max_turns":
                o.status = RunStatus.aborted
                o.error = "maximum agent turns reached"
        elif isinstance(ev, ErrorEvent):
            o.status = RunStatus.error
            o.error = ev.message

    def _over_budget(self) -> bool:
        return (
            self._budget_remaining is not None and self.outcome.cost_usd >= self._budget_remaining
        )


__all__ = ["Turn", "TurnOutcome"]
