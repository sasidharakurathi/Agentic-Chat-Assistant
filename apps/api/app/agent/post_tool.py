"""What happens to every capability's result before the model sees it
(task 1.10, the PostToolUse step).

Three jobs, all applied in one place (`run_capability`) that the real SDK
server and the offline FakeDriver both go through:

- **Size.** A tool result goes straight into the model's context. Each tool
  caps what it can (SQL rows, retrieval top-k), but nothing capped the total,
  so one enormous result could crowd out the conversation. Anything past
  `MODEL_OUTPUT_CHARS` is cut, and the model is told it was.
- **Secrets.** A query can return a row holding a credential: an `api_keys`
  table, a connection string in a config column. `deny_tables` and a
  least-privilege role are the real defences; this is the last line. Values
  shaped like well-known credential formats are replaced before they reach
  the model's context, the transcript or the logs.
- **Injection** (task 5.3, `guardrails.injection_scan`). A result that reads
  like instructions (a web page saying "ignore your rules", a document with
  hidden Unicode text) reaches the model wrapped in a warning that it is
  data from a tool, and the finding is recorded for the chat to show.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.guardrails import turn as turn_guard
from app.guardrails.injection import TOOL_NOTE, describe, reveal_hidden, scan
from app.security.redact import strip_secrets

#: ~12k tokens. Far above what any capability produces in normal use.
MODEL_OUTPUT_CHARS = 50_000


def _cap(text: str) -> str:
    if len(text) <= MODEL_OUTPUT_CHARS:
        return text
    cut = len(text) - MODEL_OUTPUT_CHARS
    return (
        text[:MODEL_OUTPUT_CHARS] + f"\n\n[truncated: {cut:,} more characters were not returned. "
        "Narrow the request if you need them.]"
    )


def finish(result: dict[str, Any]) -> dict[str, Any]:
    """Apply both steps to every text block of an MCP tool result."""
    content = [
        {**block, "text": _cap(strip_secrets(block["text"]))}
        if block.get("type") == "text" and isinstance(block.get("text"), str)
        else block
        for block in result.get("content", [])
    ]
    return {**result, "content": content}


ToolHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


def flag_injection(result: dict[str, Any], tool: str | None = None) -> dict[str, Any]:
    """Wrap an instruction-like result in a warning, when the turn scans."""
    guard = turn_guard.current()
    if guard is None or not guard.injection_scan:
        return result
    blocks = result.get("content", [])
    texts = [
        b["text"] for b in blocks if b.get("type") == "text" and isinstance(b.get("text"), str)
    ]
    signals = scan("\n".join(texts))
    if not signals:
        return result
    guard.record(
        "injection",
        "tool_result",
        f"The result read like instructions ({describe(signals)}); the model was told it is "
        "data and not to follow it.",
        tool=tool,
    )
    note = TOOL_NOTE.format(signals=describe(signals))
    content: list[Any] = []
    for block in blocks:
        text = block.get("text")
        if block.get("type") == "text" and isinstance(text, str):
            # Hidden tag characters are shown as the text they hide. The
            # warning leads the first text block rather than being a block
            # of its own: some readers only look at the first one, and the
            # data must still arrive, just marked.
            text = reveal_hidden(text)
            if note:
                text, note = f"{note}\n\n{text}", ""
            content.append({**block, "text": text})
        else:
            content.append(block)
    return {**result, "content": content}


async def run_capability(
    handler: ToolHandler, args: dict[str, Any], tool: str | None = None
) -> dict[str, Any]:
    return flag_injection(finish(await handler(args)), tool)


__all__ = ["MODEL_OUTPUT_CHARS", "finish", "flag_injection", "run_capability", "strip_secrets"]
