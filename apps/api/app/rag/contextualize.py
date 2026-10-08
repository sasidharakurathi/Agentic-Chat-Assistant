"""Contextual retrieval: a short LLM-written prefix that situates each chunk
inside its document before it gets embedded.

The problem it solves: chunking destroys context. A chunk reading *"This
applies for thirty days from purchase."* is nearly unretrievable — "this"
and "purchase" of *what*? Prefixing it with *"From the Warranty Terms
section of the Employee Handbook, describing the return window:"* gives both
the embedding and the keyword index something to match on. That is
Anthropic's contextual-retrieval technique, and it is the plan's §5.1 step 4.

**This is the first thing in the whole project that spends money on its
own** — one cheap model call per chunk, during ingestion, triggered by a file
upload rather than by someone pressing send. So it is off unless three things
agree: the assistant's config asks for it, the environment isn't pinned to
the free/offline path (``AGENT_DRIVER=fake`` / ``RAG_OFFLINE=1``), and a key
actually exists. Any one of those says no and ingestion silently uses the
free path — `NullContextualizer` — with no prefix and no spend.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

from anthropic import AsyncAnthropic

from app.agent import claude_api
from app.agent.models import CONTEXTUALIZE_MODEL, price_per_mtok, resolve_model
from app.config import settings
from app.logging import get_logger

log = get_logger(__name__)

# Haiku, per the plan: this runs once per chunk, so a big model would make
# ingestion cost more than everything else in the pipeline combined.
_MODEL = CONTEXTUALIZE_MODEL

# How much of the document to show the model as context. The whole document
# would be both slow and expensive for a job that runs per chunk.
DOC_CONTEXT_CHARS = 6_000

# At most this many chunks in flight. Ingestion of a large PDF can produce
# hundreds of chunks; firing them all at once is a good way to get rate
# limited and to make one slow upload starve every other worker job.
MAX_CONCURRENCY = 5

#: Chunks per request. The document excerpt is the bulk of every request, so
#: sending it once for eight chunks instead of once per chunk cuts input
#: tokens (and so cost) by roughly that factor.
BATCH_SIZE = 8

#: Rough output per context line, for the budget estimate.
_EST_OUT_TOKENS_PER_CHUNK = 60

_PROMPT = (
    "Here is a document:\n<document>\n{doc}\n</document>\n\n"
    "Here are {n} chunks taken from it:\n{chunks}\n\n"
    "For each chunk, write a short standalone context line (at most two sentences) that "
    "situates it within the document, so it can be understood and searched on its own. "
    "Resolve pronouns and vague references to what they actually refer to. "
    "Answer with one line per chunk, in exactly this form and nothing else:\n"
    '<context index="1">...</context>'
)
_CONTEXT_TAG = re.compile(r'<context index="(\d+)">(.*?)</context>', re.DOTALL)


@dataclass
class ContextResult:
    """Prefixes aligned 1:1 with the chunks passed in (``None`` = no prefix),
    plus what producing them cost."""

    prefixes: list[str | None]
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    #: Chunks whose request failed even after retries (they get no prefix).
    failed: int = 0

    @property
    def written(self) -> int:
        return sum(1 for p in self.prefixes if p)


class NullContextualizer:
    """The free path: every chunk gets no prefix.

    Not a failure mode — it is what the pipeline did before this task, and
    what it keeps doing whenever contextualisation is off.
    """

    name = "none"
    enabled = False

    def estimate_cost(self, document_text: str, chunks: list[str]) -> float:
        return 0.0

    async def contextualize(self, document_text: str, chunks: list[str]) -> ContextResult:
        return ContextResult(prefixes=[None] * len(chunks))


def _parse(text: str, n: int) -> list[str | None]:
    found: dict[int, str] = {}
    for index, body in _CONTEXT_TAG.findall(text):
        line = body.strip()
        if line and 1 <= int(index) <= n:
            found[int(index)] = line
    if not found and n == 1 and text.strip() and "<" not in text:
        # A one-chunk answer without the tags is still unambiguous.
        return [text.strip()]
    return [found.get(i) for i in range(1, n + 1)]


class HaikuContextualizer:
    """Haiku calls of up to `BATCH_SIZE` chunks each, bounded concurrency,
    failures isolated to their batch.

    The Messages API through the official SDK (`agent/claude_api.py`), not
    the Agent SDK: this is a one-shot completion with no tools, no session
    and no streaming. The SDK retries rate limits and overloads itself,
    waiting as long as the API asks.
    """

    name = _MODEL
    enabled = True

    def __init__(
        self, model: str = _MODEL, concurrency: int = MAX_CONCURRENCY, batch_size: int = BATCH_SIZE
    ) -> None:
        self.model = model
        self.batch_size = max(1, batch_size)
        self._sem = asyncio.Semaphore(concurrency)

    def _groups(self, chunks: list[str]) -> list[list[str]]:
        return [chunks[i : i + self.batch_size] for i in range(0, len(chunks), self.batch_size)]

    def estimate_cost(self, document_text: str, chunks: list[str]) -> float:
        """An upper-ish estimate, made before spending anything, for the
        ingestion budget (see `ingest._context_budget_note`)."""
        doc_chars = min(len(document_text), DOC_CONTEXT_CHARS) + len(_PROMPT)
        calls = len(self._groups(chunks))
        tokens_in = (calls * doc_chars + sum(len(c) for c in chunks)) / 4
        tokens_out = _EST_OUT_TOKENS_PER_CHUNK * len(chunks)
        rate_in, rate_out = price_per_mtok(self.model)
        return round(rate_in * tokens_in / 1e6 + rate_out * tokens_out / 1e6, 6)

    async def _batch(
        self, api: AsyncAnthropic, doc: str, group: list[str]
    ) -> tuple[list[str | None], int, int, bool]:
        chunks = "\n".join(
            f'<chunk index="{i}">\n{chunk}\n</chunk>' for i, chunk in enumerate(group, 1)
        )
        async with self._sem:
            try:
                response = await api.messages.create(
                    model=resolve_model(self.model),
                    max_tokens=150 * len(group),
                    messages=[
                        {
                            "role": "user",
                            "content": _PROMPT.format(doc=doc, n=len(group), chunks=chunks),
                        }
                    ],
                )
            except Exception as exc:
                # One batch failing (after the SDK's retries) must not cost the
                # document its ingestion: missing prefixes degrade retrieval
                # slightly, a raised exception loses the whole source.
                log.warning("contextualize_batch_failed", error=type(exc).__name__)
                return [None] * len(group), 0, 0, True

        tokens_in, tokens_out = response.usage.input_tokens, response.usage.output_tokens
        if response.stop_reason == "refusal":
            # Whatever it wrote is no context line (and a one-chunk refusal
            # would otherwise pass for one); the tokens were still billed.
            log.warning("contextualize_batch_refused")
            return [None] * len(group), tokens_in, tokens_out, True
        text = "".join(b.text for b in response.content if b.type == "text")
        return _parse(text, len(group)), tokens_in, tokens_out, False

    async def contextualize(self, document_text: str, chunks: list[str]) -> ContextResult:
        if not chunks:
            return ContextResult(prefixes=[])
        doc = document_text[:DOC_CONTEXT_CHARS]
        groups = self._groups(chunks)
        async with claude_api.client(60.0) as api:
            results = await asyncio.gather(*(self._batch(api, doc, g) for g in groups))

        prefixes = [p for r in results for p in r[0]]
        tokens_in = sum(r[1] for r in results)
        tokens_out = sum(r[2] for r in results)
        failed = sum(len(g) for g, r in zip(groups, results, strict=True) if r[3])
        rate_in, rate_out = price_per_mtok(self.model)
        cost = round(rate_in * tokens_in / 1e6 + rate_out * tokens_out / 1e6, 6)
        return ContextResult(
            prefixes=prefixes,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost,
            failed=failed,
        )


def get_contextualizer(enabled_in_config: bool) -> NullContextualizer | HaikuContextualizer:
    """Same auto-detection shape as ``get_driver`` / ``get_embedder``.

    Three independent vetoes, because this one spends money without anyone
    pressing send:

    * the assistant's own ``rag.contextual_retrieval`` setting,
    * the environment's free-path pins — ``RAG_OFFLINE=1`` (no paid RAG
      calls) or ``AGENT_DRIVER=fake`` (no real model calls), either of which
      is a deliberate "don't spend" signal,
    * an actual key.
    """
    if not enabled_in_config:
        return NullContextualizer()
    if settings.rag_offline or settings.agent_driver == "fake":
        return NullContextualizer()
    if settings.app_env == "test" or not settings.anthropic_api_key:
        return NullContextualizer()
    return HaikuContextualizer()


def apply_prefix(prefix: str | None, content: str) -> str:
    """What actually gets embedded. The prefix is prepended for the vector
    (and, in ``PgVectorStore.upsert``, concatenated for the ``tsv``) but the
    chunk's stored ``content`` stays untouched — a citation must quote what
    the document really said, not a model's paraphrase of where it sits."""
    return f"{prefix}\n\n{content}" if prefix else content


__all__ = [
    "BATCH_SIZE",
    "DOC_CONTEXT_CHARS",
    "MAX_CONCURRENCY",
    "ContextResult",
    "HaikuContextualizer",
    "NullContextualizer",
    "apply_prefix",
    "get_contextualizer",
]
