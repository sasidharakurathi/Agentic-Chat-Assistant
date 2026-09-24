"""What happens to every capability's result before the model sees it
(task 1.10, the PostToolUse step).

Two jobs, both applied in one place (`run_capability`) that the real SDK
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
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

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


async def run_capability(handler: ToolHandler, args: dict[str, Any]) -> dict[str, Any]:
    return finish(await handler(args))


__all__ = ["MODEL_OUTPUT_CHARS", "finish", "run_capability", "strip_secrets"]
