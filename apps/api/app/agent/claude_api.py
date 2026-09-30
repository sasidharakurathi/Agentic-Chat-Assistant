"""Direct calls to the Claude API, outside agent turns (tasks 5.2, 5.5).

Agent turns go through the Agent SDK (`agent/driver.py`). Everything else
that asks Claude one question and reads one answer goes through here, on
the official `anthropic` SDK: conversation summaries and titles, contextual
retrieval prefixes, and the builder's AI assist (writing a system prompt).
One client, the SDK's typed errors and its retries (connection errors, 408,
409, 429 and 5xx, with backoff).

**Whether to spend at all** is decided in one place, `real_model_allowed()`:
only when this instance actually runs the real agent. With the fake driver
(`AGENT_DRIVER=fake`, or `auto` without a key, or under test) every caller
uses its free, deterministic stand-in instead, so a developer pinned to the
free path never pays for a summary, a title or a generated prompt either.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from anthropic import AsyncAnthropic

from app.agent.models import PRICE_PER_MTOK
from app.config import settings

#: Retries the SDK makes itself (connection errors, 408/409/429/5xx).
MAX_RETRIES = 2


def real_model_allowed() -> bool:
    """True only when the agent itself would call the real model."""
    from app.agent.driver import get_driver

    return get_driver().name != "fake" and bool(settings.anthropic_api_key)


def _new_client(timeout_s: float) -> AsyncAnthropic:
    return AsyncAnthropic(
        api_key=settings.anthropic_api_key, timeout=timeout_s, max_retries=MAX_RETRIES
    )


#: Builds the client. Tests put a stand-in here; nothing in the test suite
#: ever reaches the real API.
client_factory: Callable[[float], AsyncAnthropic] = _new_client


def client(timeout_s: float = 60.0) -> AsyncAnthropic:
    return client_factory(timeout_s)


def cost_usd(model: str, usage: Any) -> float:
    """What a response cost, from its `usage` and `PRICE_PER_MTOK`."""
    tokens_in = int(getattr(usage, "input_tokens", 0) or 0)
    tokens_out = int(getattr(usage, "output_tokens", 0) or 0)
    rate_in, rate_out = PRICE_PER_MTOK.get(model, (0.0, 0.0))
    return round(rate_in * tokens_in / 1e6 + rate_out * tokens_out / 1e6, 6)


class Refused(Exception):
    """The model declined the request (`stop_reason == "refusal"`)."""


@dataclass
class Completion:
    text: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0


async def complete(
    prompt: str, *, model: str, max_tokens: int, timeout_s: float = 60.0
) -> Completion:
    """One user prompt, one text answer. Raises on failure (the SDK's typed
    errors, or `Refused`): callers decide whether a failed summary or title
    matters, and neither ever fails a turn."""
    async with client(timeout_s) as api:
        response = await api.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
    if response.stop_reason == "refusal":
        raise Refused(model)
    text = "".join(b.text for b in response.content if b.type == "text").strip()
    return Completion(
        text=text,
        model=model,
        tokens_in=response.usage.input_tokens,
        tokens_out=response.usage.output_tokens,
        cost_usd=cost_usd(model, response.usage),
    )


__all__ = [
    "MAX_RETRIES",
    "Completion",
    "Refused",
    "client",
    "client_factory",
    "complete",
    "cost_usd",
    "real_model_allowed",
]
