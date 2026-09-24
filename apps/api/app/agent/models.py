"""The single source of truth for Claude model ids the platform allows.

Keep every model-id string in this module. `AssistantConfig` validates against
`ALLOWED_MODELS`; the runtime (Phase 1.4+) reads the defaults here.
"""

from __future__ import annotations

from typing import Literal

# Roles an assistant assigns a model to.
ModelRole = Literal["router", "main", "subagent", "judge"]

# Allowed model ids (Anthropic-only in v1 — see ADR 0001).
ALLOWED_MODELS: frozenset[str] = frozenset(
    {
        "claude-haiku-4-5",
        "claude-sonnet-5",
        "claude-opus-5",
    }
)

# Default model per role.
DEFAULT_MODEL_BY_ROLE: dict[str, str] = {
    "router": "claude-haiku-4-5",
    "main": "claude-sonnet-5",
    "subagent": "claude-haiku-4-5",
    "judge": "claude-opus-5",
}

# The cheap model used by non-chat internals (contextual-retrieval prefixes,
# task 2.6). Named here rather than inline so this module stays the only
# place a model-id string lives — and so its cost lands in PRICE_PER_MTOK
# instead of silently pricing at zero.
CONTEXTUALIZE_MODEL = "claude-haiku-4-5"

# Rough USD-per-1M-token rates, for pre-flight budget estimates only (not billing).
# Kept here so there is one place to update when prices change.
PRICE_PER_MTOK: dict[str, tuple[float, float]] = {
    # model: (input, output)
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-opus-5": (5.00, 25.00),
}


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
    "CONTEXTUALIZE_MODEL",
    "DEFAULT_MODEL_BY_ROLE",
    "PRICE_PER_MTOK",
    "RAG_PRICE_PER_MTOK",
    "ModelRole",
    "is_allowed",
]
