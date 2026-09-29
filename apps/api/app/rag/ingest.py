"""The orchestration task 2.1-2.4 were built for: fetch a pending
``DataSource`` -> parse -> chunk -> contextualize -> embed -> write ``Chunk``
rows -> update status. This is a plain async function, not an Arq job itself —
``app/worker.py`` wraps it in one so it's independently testable without a
running worker.

Re-running this for a source that already has a ``Document`` (a "reindex")
deletes that document first — its chunks cascade-delete via the FK, so a
transaction never has both old and new chunks for the same source at once.

Plan §5.1, points this module is responsible for:

- **Stable ids.** The document id derives from the source, and a chunk id
  from ``(document_id, ordinal, content)``. A reindex of unchanged content
  writes the same ids back, so a citation saved in an old answer still
  resolves to its chunk.
- **Nothing paid for twice.** Before the old chunks are dropped, their context
  lines and vectors are kept by ``(ordinal, content)``. A context line is
  reused when the document's checksum is unchanged (it describes the chunk's
  place in *that* document); a vector when the model and the exact text
  embedded are unchanged. Reindexing an unchanged source costs nothing.
- **A budget for context.** Contextual retrieval is estimated before it runs;
  over ``INGEST_CONTEXT_BUDGET_USD`` the source is indexed without context
  lines rather than not at all, and the report says why.
- **Progress.** Each stage and batch is reported to ``app.rag.progress``.
- **A report.** ``data_sources.ingest_report`` records what the run did.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.assistant import Assistant
from app.models.rag import Chunk, DataSource, DataSourceStatus, DataSourceType, Document
from app.models.usage import UsageEvent, UsageKind
from app.rag import progress, usage
from app.rag.chunking import ChunkSpan, chunk_document
from app.rag.contextualize import ContextResult, apply_prefix, get_contextualizer
from app.rag.embedders import Embedder, get_embedder
from app.rag.embedders.voyage import batches
from app.rag.fetch import UrlFetchError, fetch_url_text
from app.rag.parsers import ParsedDocument, parse_by_mime, parse_text
from app.rag.usage import Meter
from app.rag.vectorstore import ChunkRecord, PgVectorStore
from app.schemas.assistant_config import AssistantConfig, RagConfig
from app.storage import get_object

#: Texts per embedding step (and progress report).
PROGRESS_STEP = 32

#: Namespace for the derived document and chunk ids (uuid5).
_ID_NAMESPACE = uuid.UUID("7d0f3c52-5a0e-4c7e-9f3b-2f6b1a9c4e11")


class IngestError(RuntimeError):
    pass


@dataclass
class Fetched:
    parsed: ParsedDocument
    mime: str
    #: sha256 of what was fetched: the file's bytes, the page's or the pasted text.
    checksum: str


@dataclass
class IngestReport:
    """What one ingestion did. Stored on the source for the sources page."""

    chunks: int = 0
    embedding_model: str = ""
    embedded: int = 0
    reused_embeddings: int = 0
    contextualized: int = 0
    reused_context: int = 0
    context_failed: int = 0
    #: Why contextual retrieval did not run although the assistant asked for it.
    context_skipped: str | None = None
    cost_usd: float = 0.0


@dataclass
class _Cached:
    prefix: str | None
    embedding: list[float]
    model: str | None
    doc_checksum: str | None


def _sha(text: str | bytes) -> str:
    return hashlib.sha256(text.encode("utf-8") if isinstance(text, str) else text).hexdigest()


def document_id_for(data_source_id: uuid.UUID) -> uuid.UUID:
    """One source produces one document, and always the same id."""
    return uuid.uuid5(_ID_NAMESPACE, f"document:{data_source_id}")


def chunk_id_for(document_id: uuid.UUID, ordinal: int, content: str) -> uuid.UUID:
    """Plan §5.1: chunk id = hash(document_id, ordinal, content)."""
    return uuid.uuid5(_ID_NAMESPACE, f"chunk:{document_id}:{ordinal}:{_sha(content)}")


def _model_of(embedder: Embedder) -> str:
    return str(getattr(embedder, "model", None) or embedder.name)


async def _fetch(source: DataSource) -> Fetched:
    config = source.config or {}
    if source.type == DataSourceType.file:
        if not source.object_key:
            raise IngestError("file data source has no object_key")
        content = await get_object(source.object_key)
        mime = str(config.get("content_type", "application/octet-stream"))
        return Fetched(parse_by_mime(content, mime=mime, filename=source.name), mime, _sha(content))

    if source.type == DataSourceType.url:
        if not source.uri:
            raise IngestError("url data source has no uri")
        try:
            text = await fetch_url_text(source.uri)
        except UrlFetchError as exc:
            raise IngestError(str(exc)) from exc
        if not text.strip():
            raise IngestError(f"no extractable content at {source.uri}")
        return Fetched(ParsedDocument(text=text, title=source.name), "text/html", _sha(text))

    # text
    text = str(config.get("text", ""))
    if not text.strip():
        raise IngestError("text data source has no content")
    return Fetched(parse_text(text.encode("utf-8")), "text/plain", _sha(text))


def _record_context_cost(
    session: AsyncSession, source: DataSource, model: str, result: ContextResult
) -> None:
    """Contextualisation spends money during *ingestion*, not during a chat
    turn, so it never passes through ``chat.run_message``'s accounting. It
    lands on the same ``usage_events`` ledger anyway, or the org's usage
    endpoint would under-report what the assistant actually cost."""
    session.add(
        UsageEvent(
            org_id=source.org_id,
            assistant_id=source.assistant_id,
            conversation_id=None,
            kind=UsageKind.llm,
            model=model,
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            cost_usd=result.cost_usd,
        )
    )


async def _rag_settings(session: AsyncSession, assistant_id: uuid.UUID) -> RagConfig:
    assistant = await session.get(Assistant, assistant_id)
    if assistant is None:
        return RagConfig()
    return AssistantConfig.model_validate(assistant.draft_config).rag


def _usage_rows(source: DataSource, used: Meter) -> list[UsageEvent]:
    return [
        UsageEvent(
            org_id=source.org_id,
            assistant_id=source.assistant_id,
            conversation_id=None,
            kind=UsageKind(line.kind),
            model=line.model,
            tokens_in=line.tokens,
            tokens_out=0,
            cost_usd=line.cost_usd,
        )
        for line in used.rows()
    ]


async def _load_cache(
    session: AsyncSession, documents: list[Document]
) -> dict[tuple[int, str], _Cached]:
    """What the previous index of this source already paid for."""
    if not documents:
        return {}
    checksums = {d.id: d.checksum for d in documents}
    rows = await session.execute(
        select(
            Chunk.document_id,
            Chunk.ordinal,
            Chunk.content,
            Chunk.context_prefix,
            Chunk.embedding,
            Chunk.chunk_metadata,
        ).where(Chunk.document_id.in_(list(checksums)))
    )
    cache: dict[tuple[int, str], _Cached] = {}
    for doc_id, ordinal, content, prefix, embedding, meta in rows.all():
        if embedding is None:
            continue
        cache[(ordinal, _sha(content))] = _Cached(
            prefix=prefix,
            # pgvector returns a numpy array; it goes back in as a list.
            embedding=[float(x) for x in embedding],
            model=(meta or {}).get("embedding_model"),
            doc_checksum=checksums[doc_id],
        )
    return cache


async def _drop_previous_index(
    session: AsyncSession, source: DataSource
) -> dict[tuple[int, str], _Cached]:
    """Reindex safety: drop any document(s) this source produced before, so
    their chunks are gone before the fresh ones are written (in the same
    transaction). What they cost to make is kept first, and returned."""
    existing = list(
        await session.scalars(select(Document).where(Document.data_source_id == source.id))
    )
    cache = await _load_cache(session, existing)
    for doc in existing:
        await session.delete(doc)
    await session.flush()
    # The database cascades the old chunks away; any this session had loaded
    # would still sit in its identity map under the ids the new chunks are
    # about to reuse.
    gone = {doc.id for doc in existing}
    for obj in list(session.identity_map.values()):
        if isinstance(obj, Chunk) and obj.document_id in gone:
            session.expunge(obj)
    return cache


async def _context_prefixes(
    session: AsyncSession,
    source: DataSource,
    fetched: Fetched,
    spans: list[ChunkSpan],
    rag: RagConfig,
    cache: dict[tuple[int, str], _Cached],
    report: IngestReport,
) -> tuple[list[str | None], tuple[str, ContextResult] | None]:
    """A context line per span: reused, freshly written, or none."""
    prefixes: list[str | None] = [None] * len(spans)
    contextualizer = get_contextualizer(rag.contextual_retrieval)
    if not contextualizer.enabled:
        return prefixes, None

    missing: list[int] = []
    for i, span in enumerate(spans):
        hit = cache.get((span.ordinal, _sha(span.text)))
        if hit is not None and hit.prefix and hit.doc_checksum == fetched.checksum:
            prefixes[i] = hit.prefix
            report.reused_context += 1
        else:
            missing.append(i)
    if not missing:
        return prefixes, None

    texts = [spans[i].text for i in missing]
    estimate = contextualizer.estimate_cost(fetched.parsed.text, texts)
    budget = settings.ingest_context_budget_usd
    if estimate > budget:
        report.context_skipped = (
            f"estimated ${estimate:.2f} for {len(texts)} chunks is over the "
            f"${budget:.2f} per-source budget (INGEST_CONTEXT_BUDGET_USD)"
        )
        return prefixes, None

    await progress.report(source.id, "contextualizing", 0, len(texts))
    result = await contextualizer.contextualize(fetched.parsed.text, texts)
    for i, prefix in zip(missing, result.prefixes, strict=True):
        prefixes[i] = prefix
    report.contextualized += result.written
    report.context_failed += result.failed
    spent: tuple[str, ContextResult] | None = None
    if result.cost_usd or result.tokens_in:
        spent = (contextualizer.name, result)
        _record_context_cost(session, source, contextualizer.name, result)
    return prefixes, spent


async def _embeddings(
    source: DataSource,
    embedder: Embedder,
    spans: list[ChunkSpan],
    prefixes: list[str | None],
    cache: dict[tuple[int, str], _Cached],
    report: IngestReport,
) -> list[list[float]]:
    model = _model_of(embedder)
    vectors: list[list[float] | None] = [None] * len(spans)
    todo: list[int] = []
    for i, (span, prefix) in enumerate(zip(spans, prefixes, strict=True)):
        hit = cache.get((span.ordinal, _sha(span.text)))
        # The vector is of prefix + content, so both must match, and the model.
        if hit is not None and hit.model == model and hit.prefix == prefix:
            vectors[i] = hit.embedding
            report.reused_embeddings += 1
        else:
            todo.append(i)

    # Embedded in steps of `PROGRESS_STEP` so progress moves visibly (the
    # local CPU model takes minutes per step); the Voyage embedder packs
    # each step into as few requests as its limits allow.
    texts = [apply_prefix(prefixes[i], spans[i].text) for i in todo]
    done = 0
    await progress.report(source.id, "embedding", 0, len(texts))
    for batch in batches(texts, max_texts=PROGRESS_STEP):
        fresh = await embedder.embed_documents(batch)
        for i, vector in zip(todo[done : done + len(batch)], fresh, strict=True):
            vectors[i] = vector
        done += len(batch)
        await progress.report(source.id, "embedding", done, len(texts))
    report.embedded = len(todo)
    return [v for v in vectors if v is not None]


async def ingest_data_source(session: AsyncSession, data_source_id: uuid.UUID) -> None:
    source = await session.get(DataSource, data_source_id)
    if source is None:
        return

    source.status = DataSourceStatus.processing
    source.error = None
    await session.commit()
    await progress.report(source.id, "fetching")
    # Every embed call below reports to this meter (the job runs in its own
    # task, so activating it there scopes it to this ingestion).
    used = Meter()
    spent_context: tuple[str, ContextResult] | None = None
    usage.activate(used)
    try:
        fetched = await _fetch(source)
        rag = await _rag_settings(session, source.assistant_id)
        await progress.report(source.id, "chunking")
        spans = chunk_document(
            fetched.parsed, max_tokens=rag.chunking.max_tokens, overlap=rag.chunking.overlap
        )
        embedder = get_embedder()
        report = IngestReport(chunks=len(spans), embedding_model=_model_of(embedder))

        cache = await _drop_previous_index(session, source)

        document = Document(
            id=document_id_for(source.id),
            data_source_id=source.id,
            assistant_id=source.assistant_id,
            org_id=source.org_id,
            title=fetched.parsed.title or source.name,
            source_uri=source.uri,
            mime=fetched.mime,
            checksum=fetched.checksum,
            page_count=fetched.parsed.page_count,
            token_count=sum(s.token_count for s in spans),
        )
        session.add(document)
        await session.flush()

        if spans:
            prefixes, spent_context = await _context_prefixes(
                session, source, fetched, spans, rag, cache, report
            )
            vectors = await _embeddings(source, embedder, spans, prefixes, cache, report)
            await progress.report(source.id, "writing")
            records = [
                ChunkRecord(
                    id=chunk_id_for(document.id, span.ordinal, span.text),
                    document_id=document.id,
                    assistant_id=source.assistant_id,
                    org_id=source.org_id,
                    ordinal=span.ordinal,
                    # The prefix is embedded with the chunk but never replaces
                    # its stored `content` — a citation has to quote the
                    # document, not a model's description of it.
                    content=span.text,
                    context_prefix=prefix,
                    embedding=vector,
                    token_count=span.token_count,
                    # `start`/`end` are char offsets into the document's
                    # normalized text — what a citation needs to say "this
                    # came from characters 4100-4900 of the source."
                    metadata={
                        "breadcrumb": span.breadcrumb,
                        "page": span.page,
                        "page_end": span.page_end,
                        "start": span.start,
                        "end": span.end,
                        # Which model made the vector: a reindex reuses it
                        # only while that is still the model in use.
                        "embedding_model": report.embedding_model,
                    },
                )
                for span, vector, prefix in zip(spans, vectors, prefixes, strict=True)
            ]
            await PgVectorStore().upsert(session, records)

        session.add_all(_usage_rows(source, used))
        report.cost_usd = round(
            used.cost_usd + (spent_context[1].cost_usd if spent_context else 0.0), 6
        )
        source.status = DataSourceStatus.ready
        source.indexed_at = datetime.now(UTC)
        source.ingest_report = _report_json(report)
        await session.commit()
    except asyncio.CancelledError:
        # The worker's job timeout, or a shutdown. Not an `Exception`, so it
        # used to skip the handler below and leave the source "processing"
        # for good, with nothing working on it. (Observed: a 47-chunk source
        # on the local CPU model outran arq's default 300 s.)
        await _record_failure(session, data_source_id, STOPPED, spent_context, used)
        raise
    except Exception as exc:
        await _record_failure(session, data_source_id, str(exc), spent_context, used)
        raise
    finally:
        await progress.clear(data_source_id)


#: What a source says when its ingestion was cancelled part-way.
STOPPED = (
    "Indexing was stopped before it finished (it ran past the worker's time limit, "
    "or the worker shut down). Use Reindex to try again."
)


async def _record_failure(
    session: AsyncSession,
    data_source_id: uuid.UUID,
    message: str,
    spent_context: tuple[str, ContextResult] | None,
    used: Meter,
) -> None:
    await session.rollback()
    source = await session.get(DataSource, data_source_id)
    if source is None:  # pragma: no cover - deleted mid-ingest
        return
    source.status = DataSourceStatus.error
    source.error = message[:2000]
    # Spend that already happened is recorded even though indexing failed.
    # It used to be added to the same transaction and rolled back with it,
    # so a failed ingest looked free although every call was billed.
    if spent_context is not None:
        _record_context_cost(session, source, *spent_context)
    session.add_all(_usage_rows(source, used))
    await session.commit()


def _report_json(report: IngestReport) -> dict[str, Any]:
    return asdict(report)


__all__ = [
    "IngestError",
    "IngestReport",
    "chunk_id_for",
    "document_id_for",
    "ingest_data_source",
]
