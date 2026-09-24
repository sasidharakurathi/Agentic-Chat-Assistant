"""``kb_search`` / ``kb_list_sources`` — the two tools that let an agent
actually use the knowledge base 2.1-2.7 built. Unlike ``app/agent/caps.py``'s
``calculator``/``datetime`` (static, stateless, registered once in
``ALL_CAPS``), these need per-assistant context (which knowledge base, which
retrieval settings) — so they're built by a factory, not looked up by name,
and constructed fresh per turn in ``options.py``.

Each handler opens its own short-lived DB session (same shape as
``app/worker.py``'s job function and the SSE endpoint's own session) — a
tool call happening mid-turn is not the request's outer session's business.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.agent.caps import CapabilityTool
from app.agent.citations import CitationRegistry
from app.db.session import get_sessionmaker
from app.models.rag import DataSourceStatus
from app.rag.retrieve import retrieve
from app.schemas.assistant_config import RagConfig
from app.services import data_sources as data_sources_svc

_KB_SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "What to search for."},
        "k": {
            "type": "integer",
            "description": "Max results to return (defaults to the assistant's rerank_top_n).",
        },
        "source_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Only search these sources (ids from kb_list_sources). Omit to search all."
            ),
        },
    },
    "required": ["query"],
}


def _text(s: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": s}]}


def _err(s: str) -> dict[str, Any]:
    """A failure, flagged so the UI renders it as one. See `caps._err`."""
    return {"content": [{"type": "text", "text": s}], "is_error": True}


# Wording matters more than it looks. With a registry in play, markers are
# assigned per *answer* and continue across searches, so a second call
# genuinely returns [4] [5] [6]. Telling the model "results are numbered
# [1], [2], ..." in that mode invites it to renormalise back to [1] in its
# reply - which then resolves to the FIRST search's chunk. Wrong source, and
# no error anywhere to notice it by.
_CITED_DESCRIPTION = (
    "Search the assistant's knowledge base for passages relevant to a query. "
    "Each result is prefixed with a citation marker such as [3]. Markers are "
    "assigned per answer and continue across multiple searches, so they may "
    "not start at [1] and may not be consecutive. When you use a result, cite "
    "it inline with exactly the marker printed on that result - never renumber "
    "them, and leave a space before the bracket."
)
_PLAIN_DESCRIPTION = "Search the assistant's knowledge base for passages relevant to a query."


def build_kb_search_tool(
    assistant_id: uuid.UUID,
    rag: RagConfig,
    registry: CitationRegistry | None = None,
) -> CapabilityTool:
    allowed = [uuid.UUID(s) for s in rag.source_ids] or None
    # Keyed on the config flag, not on `registry is None`: build_runtime_spec
    # is called without a registry from tests and from non-turn paths (a
    # graph:compile dry run), and flipping the tool into no-citation mode
    # there while compose_system_prompt - which reads the same flag - still
    # instructs the model to cite would put the two out of sync.
    cite = rag.citations

    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        query = str(args.get("query", "")).strip()
        if not query:
            return _err("error: empty query")

        scope = _scope(args.get("source_ids"), allowed)
        if isinstance(scope, str):
            return _err(scope)

        config = rag.retrieval
        k = args.get("k")
        if isinstance(k, int) and 0 < k < config.rerank_top_n:
            config = config.model_copy(update={"rerank_top_n": k})

        async with get_sessionmaker()() as session:
            results = await retrieve(
                session,
                assistant_id=assistant_id,
                query=query,
                config=config,
                source_ids=scope,
            )

        if not results:
            return _text("No relevant results found in the knowledge base.")

        # With citations on, the numbering comes from the per-conversation
        # registry, so the same chunk keeps one marker for the whole session
        # and a second search continues instead of restarting at 1.
        markers = registry.register(results) if (cite and registry is not None) else []

        blocks = []
        for i, r in enumerate(results):
            loc = f", p.{r.page}" if r.page else ""
            crumb = " > ".join(r.breadcrumb)
            where = f"{r.title}{loc}" + (f" - {crumb}" if crumb else "")
            if markers:
                prefix = f"[{markers[i]}] "
            elif cite:
                prefix = f"[{i + 1}] "
            else:
                # Citations off: no markers at all. Leaving them in while the
                # system prompt no longer explains them is the worst of both
                # worlds - prose full of [1]s and no sources panel to match.
                prefix = "- "
            meta = f"source_id={r.data_source_id}, score={r.score:.2f}"
            if r.uri:
                meta += f", uri={r.uri}"
            blocks.append(f"{prefix}{where} ({meta})\n{r.content}")
        return _text("\n\n".join(blocks))

    return CapabilityTool(
        name="kb_search",
        description=_CITED_DESCRIPTION if cite else _PLAIN_DESCRIPTION,
        input_schema=_KB_SEARCH_SCHEMA,
        handler=handler,
    )


def _scope(requested: object, allowed: list[uuid.UUID] | None) -> list[uuid.UUID] | str | None:
    """The sources a search may cover: the model's `source_ids` narrowed to
    what this assistant allows (its `rag.source_ids`, when set). An error
    message (a str) if the request is malformed or leaves nothing."""
    if requested is None or requested == []:
        return allowed
    if not isinstance(requested, list):
        return "error: source_ids must be a list of ids from kb_list_sources"
    try:
        ids = [uuid.UUID(str(s)) for s in requested]
    except ValueError:
        return "error: source_ids must be ids from kb_list_sources"
    if allowed is not None:
        ids = [i for i in ids if i in allowed]
        if not ids:
            return "error: none of those sources are available to this assistant"
    return ids


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def build_kb_list_sources_tool(
    assistant_id: uuid.UUID, allowed: list[str] | None = None
) -> CapabilityTool:
    """`allowed`: the assistant's `rag.source_ids`, if it restricts search to
    some sources; listing the others would offer ids kb_search then refuses."""
    only = {uuid.UUID(s) for s in allowed} if allowed else None

    async def handler(_args: dict[str, Any]) -> dict[str, Any]:
        async with get_sessionmaker()() as session:
            sources = await data_sources_svc.list_for_assistant(session, assistant_id)
            counts = await data_sources_svc.counts_for(session, assistant_id)
        ready = [
            s
            for s in sources
            if s.status == DataSourceStatus.ready and (only is None or s.id in only)
        ]
        if not ready:
            return _text("No indexed knowledge base sources yet.")
        lines = []
        for s in ready:
            docs, chunks = counts.get(s.id, (0, 0))
            lines.append(
                f"- {s.name} (id={s.id}, type={s.type.value}, "
                f"{_plural(docs, 'doc')}, {_plural(chunks, 'chunk')})"
            )
        return _text("\n".join(lines))

    return CapabilityTool(
        name="kb_list_sources",
        description="List the knowledge base sources currently indexed and searchable.",
        input_schema={"type": "object", "properties": {}},
        handler=handler,
    )


__all__ = ["build_kb_list_sources_tool", "build_kb_search_tool"]
