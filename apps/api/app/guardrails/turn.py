"""One turn's guardrails: its settings, and what they found (task 5.3).

Created by the turn from the assistant's `guardrails` config. It reaches:

- the PreToolUse gate (`agent/hooks.py`) explicitly, on the runtime spec;
- the post-tool step (`agent/post_tool.py`) through a context variable,
  because that step sits under every capability, several layers down, and
  asyncio tasks copy the context when they are created: activated in the
  turn's pump task, it is seen by every tool call the turn makes (the same
  way the RAG usage meter works).

Every finding is recorded once: kept for the saved answer (so the chat can
show it after a reload) and emitted live as a `guardrail` event.
"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Literal

Check = Literal["injection", "exfiltration", "pii", "schema", "budget"]
Where = Literal["user_message", "tool_input", "tool_result"]


@dataclass
class TurnGuard:
    injection_scan: bool = True
    pii_redaction: bool = True
    findings: list[dict[str, Any]] = field(default_factory=list)
    #: Called for each finding as it happens (the turn emits an event).
    on_finding: Callable[[dict[str, Any]], None] | None = None

    def record(
        self, check: Check, where: Where, detail: str, tool: str | None = None
    ) -> dict[str, Any]:
        finding: dict[str, Any] = {"check": check, "where": where, "detail": detail}
        if tool is not None:
            finding["tool"] = tool
        self.findings.append(finding)
        if self.on_finding is not None:
            self.on_finding(finding)
        return finding


_active: ContextVar[TurnGuard | None] = ContextVar("turn_guard", default=None)


def activate(guard: TurnGuard | None) -> None:
    """For the rest of the current task (and the tasks it creates)."""
    _active.set(guard)


def current() -> TurnGuard | None:
    return _active.get()


__all__ = ["Check", "TurnGuard", "Where", "activate", "current"]
