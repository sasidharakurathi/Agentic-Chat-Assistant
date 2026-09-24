"""Metering for embedding and rerank calls (task 1.8: usage_events kinds).

`usage_events.kind` has always had `embedding` and `rerank`, and nothing ever
wrote them: every indexed document and every `kb_search` embedded and reranked
text off the books. The embedders and rerankers keep their narrow Protocols
(text in, vectors or scores out); they report what they used to whichever
*meter* is active, a context variable the caller opens around the work:

    with meter() as used:
        vectors = await embedder.embed_documents(texts)
    for row in used.rows(): ...

A context variable, not a parameter, because the calls happen several layers
down (ingest -> embedder, turn -> tool -> retrieve -> reranker), and asyncio
tasks copy the context when they are created, so a meter opened around a
turn also sees the tool calls that turn makes.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from app.agent.models import RAG_PRICE_PER_MTOK


@dataclass
class UsageLine:
    kind: str  # "embedding" | "rerank"
    model: str
    tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class Meter:
    lines: dict[tuple[str, str], UsageLine] = field(default_factory=dict)

    def add(self, kind: str, model: str, tokens: int) -> None:
        line = self.lines.setdefault((kind, model), UsageLine(kind=kind, model=model))
        line.tokens += tokens
        line.cost_usd += tokens * RAG_PRICE_PER_MTOK.get(model, 0.0) / 1_000_000

    def rows(self) -> list[UsageLine]:
        return [line for line in self.lines.values() if line.tokens]

    @property
    def cost_usd(self) -> float:
        return sum(line.cost_usd for line in self.lines.values())


_active: ContextVar[Meter | None] = ContextVar("rag_meter", default=None)


@contextmanager
def meter() -> Iterator[Meter]:
    m = Meter()
    token = _active.set(m)
    try:
        yield m
    finally:
        _active.reset(token)


def activate(m: Meter) -> None:
    """Make `m` the active meter for the current task (and the tasks it
    creates). For a long-lived task that owns its meter, like a turn's pump;
    everything else should use `meter()`."""
    _active.set(m)


def record(kind: str, model: str, tokens: int) -> None:
    """Called by embedders and rerankers. A no-op when nothing is metering."""
    m = _active.get()
    if m is not None and tokens > 0:
        m.add(kind, model, tokens)


def estimate_tokens(texts: list[str]) -> int:
    """The same len/4 heuristic used elsewhere, for models that report none."""
    return sum(max(1, len(t) // 4) for t in texts)


__all__ = ["Meter", "UsageLine", "activate", "estimate_tokens", "meter", "record"]
