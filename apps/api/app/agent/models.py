"""The single source of truth for Claude model ids the platform allows.

Keep every model-id string in this module. `AssistantConfig` validates against
`ALLOWED_MODELS`; the runtime (Phase 1.4+) reads the defaults here.

An assistant names a model one of two ways (Phase 7a.6):

- an **alias** ("haiku", "sonnet", "opus", "fable"): that family's model of
  the moment. A version approved with "sonnet" keeps working when the model
  behind it is replaced; the operator repoints it (`MODEL_ALIASES`), and no
  assistant needs a new version or a new approval. New assistants use these.
- a **pinned id** ("claude-sonnet-5-5"): exactly that model, until it
  retires. A retired id stays valid (`RETIRED_MODELS`) and runs as its
  replacement, so an approved version never stops validating because a
  model went away.

Nothing outside this module sends an alias anywhere: `resolve_model` turns
every name into a pinned id before it reaches the CLI or the Messages API.
The SDK takes "sonnet" too, but the CLI would then choose the model itself.
"""

from __future__ import annotations

from typing import Literal

from app.config import settings

# Roles an assistant assigns a model to.
ModelRole = Literal["router", "main", "subagent", "judge"]

# Pinned model ids (Anthropic-only in v1 — see ADR 0001). Checked against
# platform.claude.com/docs/en/about-claude/models/overview on 2026-10-07.
PINNED_MODELS: frozenset[str] = frozenset(
    {
        "claude-haiku-4-5",
        "claude-sonnet-5",
        "claude-sonnet-5-5",
        "claude-opus-5",
        "claude-opus-5-5",
        "claude-fable-5-1",
    }
)

#: Family alias -> the pinned id it means today. `settings.model_aliases`
#: (the MODEL_ALIASES setting) repoints any of them, for example "haiku" to
#: its successor when one ships (Haiku 4.5 may retire from 2026-10-15).
MODEL_ALIASES: dict[str, str] = {
    "haiku": "claude-haiku-4-5",
    "sonnet": "claude-sonnet-5-5",
    "opus": "claude-opus-5-5",
    "fable": "claude-fable-5-1",
}

#: A retired id -> the pinned id it now runs as. Empty today: none of the
#: models above has retired. Add a row when one does, never delete the id.
RETIRED_MODELS: dict[str, str] = {}

#: Pinned id -> its announced retirement date, for the preflight warning
#: when a published version still pins it. Empty today: none is deprecated.
DEPRECATED_MODELS: dict[str, str] = {}

# Every name an assistant may use.
ALLOWED_MODELS: frozenset[str] = PINNED_MODELS | MODEL_ALIASES.keys() | RETIRED_MODELS.keys()

#: Models whose thinking is always on: `thinking: disabled` is a 400 there
#: (Claude Opus 5.5 and Fable 5.1), so a turn on them always asks for
#: adaptive thinking whatever the assistant's setting says.
ALWAYS_THINKS: frozenset[str] = frozenset({"claude-opus-5-5", "claude-fable-5-1"})


def aliases() -> dict[str, str]:
    """The aliases as they stand, with the operator's repointing applied."""
    return {**MODEL_ALIASES, **settings.model_aliases}


def resolve_model(name: str) -> str:
    """The pinned id a model name runs as: an alias's current model, a
    retired id's replacement, or the id itself."""
    pinned = aliases().get(name, name)
    return RETIRED_MODELS.get(pinned, pinned)


def always_thinks(name: str) -> bool:
    return resolve_model(name) in ALWAYS_THINKS


# Default model per role: aliases, so new assistants follow each family.
DEFAULT_MODEL_BY_ROLE: dict[str, str] = {
    "router": "haiku",
    "main": "sonnet",
    "subagent": "haiku",
    "judge": "opus",
}

# The model the CLI switches to when the main one refuses a request or is
# unavailable (`guardrails.refusal_fallback`, task 5.4). Always a different
# model: the CLI refuses a fallback equal to the main model, and a refusal is
# a property of the model, so asking the same one again rarely helps. Keyed
# and valued by pinned id; `fallback_for` resolves first.
FALLBACK_MODEL: dict[str, str] = {
    "claude-fable-5-1": "claude-opus-5-5",
    "claude-opus-5-5": "claude-sonnet-5-5",
    "claude-sonnet-5-5": "claude-opus-5-5",
    "claude-opus-5": "claude-sonnet-5",
    "claude-sonnet-5": "claude-opus-5",
    "claude-haiku-4-5": "claude-sonnet-5-5",
}


def fallback_for(model: str) -> str | None:
    return FALLBACK_MODEL.get(resolve_model(model))


#: Pinned ids that take the Messages API's server-side refusal fallback
#: (`fallbacks: "default"`, beta), used by the AI helpers' structured calls.
SERVER_FALLBACK_MODELS: frozenset[str] = frozenset({"claude-opus-5", "claude-opus-5-5"})


# The cheap model used by non-chat internals (contextual-retrieval prefixes,
# task 2.6). Named here rather than inline so this module stays the only
# place a model-id string lives — and so its cost lands in PRICE_PER_MTOK
# instead of silently pricing at zero. Aliases: resolve before sending.
CONTEXTUALIZE_MODEL = "haiku"
# Conversation summaries and titles (task 5.2): small one-shot jobs outside
# the agent turn, so the cheap model, like contextual retrieval.
SUMMARY_MODEL = "haiku"
TITLE_MODEL = "haiku"

# USD per 1M tokens, for pre-flight budget estimates only (not billing), by
# pinned id. Kept here so there is one place to update when prices change.
# From the models overview, checked 2026-10-07.
PRICE_PER_MTOK: dict[str, tuple[float, float]] = {
    # model: (input, output)
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-5-5": (4.00, 20.00),
    "claude-fable-5-1": (10.00, 50.00),
}


def price_per_mtok(model: str) -> tuple[float, float]:
    """(input, output) USD per 1M tokens for any model name; (0, 0) for an
    unknown one."""
    return PRICE_PER_MTOK.get(resolve_model(model), (0.0, 0.0))


#: A cache read as a share of the input price: 0.1, except where listed
#: (the pricing page, checked 2026-10-07).
CACHE_READ_SHARE: dict[str, float] = {"claude-opus-5-5": 0.05, "claude-fable-5-1": 0.025}
#: A cache write as a share of the input price: the 5-minute cache, which is
#: what the CLI writes by default (a 1-hour write is 2x).
CACHE_WRITE_SHARE = 1.25
#: Web search, per search ($10 per 1,000).
WEB_SEARCH_USD = 0.01


def priced_usage(
    model: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    web_searches: int = 0,
) -> float | None:
    """What one model's usage costs at the published prices, or None for a
    model this release has no price for (Phase 7a.6).

    The platform prices turns itself rather than taking the CLI's figure:
    the bundled CLI prices a model it doesn't know yet at Opus 5's $5 / $25
    (measured on Sonnet 5.5 and Opus 5.5), which over-counts the cheaper
    ones and would under-count Fable 5.1 by half, letting budgets overspend."""
    pinned = resolve_model(model)
    rates = PRICE_PER_MTOK.get(pinned)
    if rates is None:
        return None
    rate_in, rate_out = rates
    read = CACHE_READ_SHARE.get(pinned, 0.1)
    usd = (
        input_tokens * rate_in
        + cache_write_tokens * rate_in * CACHE_WRITE_SHARE
        + cache_read_tokens * rate_in * read
        + output_tokens * rate_out
    ) / 1_000_000 + web_searches * WEB_SEARCH_USD
    return round(usd, 6)


# USD per 1M tokens for embedding and rerank models, from Voyage's pricing
# page (docs.voyageai.com/docs/pricing, checked 2026-09-24). The local models
# cost nothing and are deliberately absent: their usage is still recorded,
# priced at zero.
RAG_PRICE_PER_MTOK: dict[str, float] = {
    "voyage-3-large": 0.18,
    "rerank-2.5": 0.05,
}


def is_allowed(model: str) -> bool:
    return model in ALLOWED_MODELS


__all__ = [
    "ALLOWED_MODELS",
    "ALWAYS_THINKS",
    "CONTEXTUALIZE_MODEL",
    "DEFAULT_MODEL_BY_ROLE",
    "DEPRECATED_MODELS",
    "FALLBACK_MODEL",
    "MODEL_ALIASES",
    "PINNED_MODELS",
    "PRICE_PER_MTOK",
    "RAG_PRICE_PER_MTOK",
    "RETIRED_MODELS",
    "SERVER_FALLBACK_MODELS",
    "SUMMARY_MODEL",
    "TITLE_MODEL",
    "ModelRole",
    "aliases",
    "always_thinks",
    "fallback_for",
    "is_allowed",
    "price_per_mtok",
    "priced_usage",
    "resolve_model",
]
