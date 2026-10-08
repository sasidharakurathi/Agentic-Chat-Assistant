"""One structured answer from the assistant's main model (tasks 5.5, 5.6).

The builder's AI helpers each ask one question and want JSON back in a
known shape. This sends it with structured outputs (`output_config.format`,
a JSON schema from a pydantic model via `anthropic.transform_schema`) on
the assistant's own main model. On Claude Opus 5, with the assistant's
refusal fallback on, it also opts into the server-side fallback
(`fallbacks: "default"`).

The stop reason is checked before the JSON: a refusal is plain text, and
so is an answer cut off at the token limit. Either is an `AssistFailed`,
carrying what the call cost, because those tokens were billed too. (The
SDK's `messages.parse` would validate the JSON first and raise a pydantic
error on a refusal, so it isn't used.)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, TypeVar

from anthropic import transform_schema
from anthropic.types import Message
from anthropic.types.beta import BetaMessage
from pydantic import BaseModel, ValidationError

from app.agent import claude_api
from app.agent.models import SERVER_FALLBACK_MODELS, resolve_model
from app.schemas.assistant_config import AssistantConfig

T = TypeVar("T", bound=BaseModel)

#: Room for the answer; it is billed by what is written, not by this.
MAX_TOKENS = 16_000
TIMEOUT_S = 120.0


@dataclass
class Spend:
    """What one model call cost, for the usage ledger."""

    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0


class AssistFailed(Exception):
    """The model answered, but not usably: it declined (`refused`), or its
    answer was cut off or malformed (`unreadable`). The spend goes with it."""

    def __init__(self, reason: Literal["refused", "unreadable"], spend: Spend) -> None:
        super().__init__(reason)
        self.reason: Literal["refused", "unreadable"] = reason
        self.spend = spend


async def ask(
    config: AssistantConfig,
    *,
    system: str,
    prompt: str,
    output: type[T],
    model: str | None = None,
    max_tokens: int = MAX_TOKENS,
    timeout_s: float = TIMEOUT_S,
) -> tuple[T, Spend]:
    """`model`: the assistant's main model unless another is given (the
    router's, task 5.10)."""
    # An alias never leaves the platform (agent/models.py).
    model = resolve_model(model or config.models.main.model)
    request: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": prompt}],
        "output_config": {"format": {"type": "json_schema", "schema": transform_schema(output)}},
    }
    response: Message | BetaMessage
    async with claude_api.client(timeout_s) as api:
        if model in SERVER_FALLBACK_MODELS and config.guardrails.refusal_fallback:
            response = await api.beta.messages.create(
                **request, betas=["server-side-fallback-2026-07-01"], fallbacks="default"
            )
        else:
            response = await api.messages.create(**request)
    spend = Spend(
        model=model,
        tokens_in=response.usage.input_tokens,
        tokens_out=response.usage.output_tokens,
        cost_usd=claude_api.cost_usd(model, response.usage),
    )
    if response.stop_reason == "refusal":
        raise AssistFailed("refused", spend)
    text = "".join(b.text for b in response.content if b.type == "text")
    try:
        return output.model_validate_json(text), spend
    except ValidationError:
        raise AssistFailed("unreadable", spend) from None


__all__ = ["MAX_TOKENS", "AssistFailed", "Spend", "ask"]
