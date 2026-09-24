"""Ingestion's ids, reuse, context wiring, budget and progress (tasks 2.5, 2.6).

Real Postgres (like test_rag_ingest.py, whose fixtures these share), with a
counting embedder in place of the local model: what matters here is *which*
texts get embedded, not the vectors.
"""

from __future__ import annotations

import hashlib
import uuid

import pytest
from app.models.assistant import Assistant
from app.models.rag import Chunk, DataSource, DataSourceStatus, DataSourceType, Document
from app.models.usage import UsageEvent as UsageRow
from app.models.usage import UsageKind
from app.rag.contextualize import ContextResult
from app.rag.ingest import document_id_for, ingest_data_source
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_rag_ingest import assistant, pg  # noqa: F401 - fixtures

pytestmark = pytest.mark.integration


class _CountingEmbedder:
    """Deterministic vectors, and a record of every text it was asked for."""

    name = "counting-embedder"
    model = "counting-embedder"

    def __init__(self) -> None:
        self.texts: list[str] = []

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.texts.extend(texts)
        return [[float(len(t) % 7) + 0.5] + [0.0] * 1023 for t in texts]

    async def embed_query(self, text: str) -> list[float]:  # pragma: no cover
        return [1.0] + [0.0] * 1023


class _FakeContextualizer:
    name = "fake-haiku"
    enabled = True

    def __init__(self, estimate: float = 0.01) -> None:
        self.calls: list[list[str]] = []
        self._estimate = estimate

    def estimate_cost(self, document_text: str, chunks: list[str]) -> float:
        return self._estimate

    async def contextualize(self, document_text: str, chunks: list[str]) -> ContextResult:
        self.calls.append(chunks)
        return ContextResult(
            prefixes=[f"ctx {c[:10]}" for c in chunks],
            tokens_in=100 * len(chunks),
            tokens_out=10 * len(chunks),
            cost_usd=0.001 * len(chunks),
        )


LONG_TEXT = "\n\n".join(
    f"Section {i}. The warranty covers part {i} for {i + 1} years after purchase. " * 6
    for i in range(40)
)


async def _text_source(
    pg: AsyncSession,  # noqa: F811
    assistant: Assistant,  # noqa: F811
    text: str = LONG_TEXT,
) -> DataSource:
    source = DataSource(
        assistant_id=assistant.id,
        org_id=assistant.org_id,
        type=DataSourceType.text,
        name="warranty",
        status=DataSourceStatus.pending,
        config={"text": text},
    )
    pg.add(source)
    await pg.commit()
    return source


async def _chunks(pg: AsyncSession, source: DataSource) -> list[Chunk]:  # noqa: F811
    document = await pg.scalar(select(Document).where(Document.data_source_id == source.id))
    assert document is not None
    return list(
        await pg.scalars(
            select(Chunk).where(Chunk.document_id == document.id).order_by(Chunk.ordinal)
        )
    )


@pytest.fixture
def embedder(monkeypatch: pytest.MonkeyPatch) -> _CountingEmbedder:
    e = _CountingEmbedder()
    monkeypatch.setattr("app.rag.ingest.get_embedder", lambda: e)
    return e


async def test_the_document_records_its_type_and_checksum(
    pg: AsyncSession,  # noqa: F811
    assistant: Assistant,  # noqa: F811
    embedder: _CountingEmbedder,
) -> None:
    source = await _text_source(pg, assistant)
    await ingest_data_source(pg, source.id)
    document = await pg.scalar(select(Document).where(Document.data_source_id == source.id))
    assert document is not None
    assert document.mime == "text/plain"
    assert document.checksum == hashlib.sha256(LONG_TEXT.encode()).hexdigest()
    assert document.id == document_id_for(source.id)


async def test_reindexing_unchanged_content_keeps_ids_and_pays_nothing(
    pg: AsyncSession,  # noqa: F811
    assistant: Assistant,  # noqa: F811
    embedder: _CountingEmbedder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _FakeContextualizer()
    monkeypatch.setattr("app.rag.ingest.get_contextualizer", lambda _on: ctx)
    source = await _text_source(pg, assistant)
    await ingest_data_source(pg, source.id)
    first = await _chunks(pg, source)
    embedded_first = len(embedder.texts)
    assert embedded_first == len(first) > 3
    assert all(c.context_prefix and c.context_prefix.startswith("ctx ") for c in first)

    await ingest_data_source(pg, source.id)
    second = await _chunks(pg, source)
    assert [c.id for c in second] == [c.id for c in first], "a citation still resolves"
    assert len(embedder.texts) == embedded_first, "nothing re-embedded"
    assert len(ctx.calls) == 1, "no context line re-written"
    assert [c.context_prefix for c in second] == [c.context_prefix for c in first]

    await pg.refresh(source)
    report = source.ingest_report or {}
    assert report["reused_embeddings"] == len(second)
    assert report["reused_context"] == len(second)
    assert report["embedded"] == 0
    assert report["cost_usd"] == 0


async def test_an_edit_re_embeds_only_what_changed(
    pg: AsyncSession,  # noqa: F811
    assistant: Assistant,  # noqa: F811
    embedder: _CountingEmbedder,
) -> None:
    source = await _text_source(pg, assistant)
    await ingest_data_source(pg, source.id)
    before = len(embedder.texts)
    total = len(await _chunks(pg, source))

    source.config = {"text": LONG_TEXT + "\n\nA new closing paragraph about returns."}
    await pg.commit()
    await ingest_data_source(pg, source.id)
    fresh = len(embedder.texts) - before
    assert 0 < fresh < total, f"re-embedded {fresh} of {total}"


async def test_an_edited_document_gets_fresh_context_even_for_unchanged_chunks(
    pg: AsyncSession,  # noqa: F811
    assistant: Assistant,  # noqa: F811
    embedder: _CountingEmbedder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A context line describes a chunk's place in *that* document; once the
    document changes, an identical chunk's old line may no longer be true."""
    ctx = _FakeContextualizer()
    monkeypatch.setattr("app.rag.ingest.get_contextualizer", lambda _on: ctx)
    source = await _text_source(pg, assistant)
    await ingest_data_source(pg, source.id)
    total = len(await _chunks(pg, source))

    source.config = {"text": LONG_TEXT + "\n\nA new closing paragraph about returns."}
    await pg.commit()
    await ingest_data_source(pg, source.id)
    assert len(ctx.calls) == 2
    assert len(ctx.calls[1]) >= total


async def test_context_lines_are_written_stored_and_charged(
    pg: AsyncSession,  # noqa: F811
    assistant: Assistant,  # noqa: F811
    embedder: _CountingEmbedder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _FakeContextualizer()
    monkeypatch.setattr("app.rag.ingest.get_contextualizer", lambda _on: ctx)
    source = await _text_source(pg, assistant)
    await ingest_data_source(pg, source.id)

    chunks = await _chunks(pg, source)
    assert chunks[0].context_prefix == f"ctx {chunks[0].content[:10]}"
    # The vector was made from prefix + content; the stored content is untouched.
    assert embedder.texts[0].startswith("ctx ")
    assert not chunks[0].content.startswith("ctx ")
    rows = list(
        await pg.scalars(
            select(UsageRow).where(
                UsageRow.assistant_id == assistant.id, UsageRow.model == "fake-haiku"
            )
        )
    )
    assert len(rows) == 1
    assert rows[0].kind == UsageKind.llm
    assert float(rows[0].cost_usd) == pytest.approx(0.001 * len(chunks))


async def test_over_budget_context_is_skipped_not_fatal(
    pg: AsyncSession,  # noqa: F811
    assistant: Assistant,  # noqa: F811
    embedder: _CountingEmbedder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _FakeContextualizer(estimate=5.0)
    monkeypatch.setattr("app.rag.ingest.get_contextualizer", lambda _on: ctx)
    monkeypatch.setattr("app.rag.ingest.settings.ingest_context_budget_usd", 1.0)
    source = await _text_source(pg, assistant)
    await ingest_data_source(pg, source.id)

    await pg.refresh(source)
    assert source.status == DataSourceStatus.ready
    assert ctx.calls == []
    assert all(c.context_prefix is None for c in await _chunks(pg, source))
    skipped = (source.ingest_report or {})["context_skipped"]
    assert "over the $1.00 per-source budget" in skipped


async def test_progress_is_reported_stage_by_stage_and_cleared(
    pg: AsyncSession,  # noqa: F811
    assistant: Assistant,  # noqa: F811
    embedder: _CountingEmbedder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[str, int | None, int | None]] = []
    cleared: list[uuid.UUID] = []

    async def report(
        _id: uuid.UUID, stage: str, done: int | None = None, total: int | None = None
    ) -> None:
        seen.append((stage, done, total))

    async def clear(source_id: uuid.UUID) -> None:
        cleared.append(source_id)

    monkeypatch.setattr("app.rag.ingest.progress.report", report)
    monkeypatch.setattr("app.rag.ingest.progress.clear", clear)
    source = await _text_source(pg, assistant)
    await ingest_data_source(pg, source.id)

    stages = [s for s, _, _ in seen]
    assert stages[:2] == ["fetching", "chunking"]
    assert stages[-1] == "writing"
    embedding = [(d, t) for s, d, t in seen if s == "embedding"]
    assert embedding[0][0] == 0
    assert embedding[-1][0] == embedding[-1][1]
    assert embedding[-1][1] is not None and embedding[-1][1] > 0
    assert cleared == [source.id]


async def test_a_timed_out_ingest_does_not_stay_processing(
    pg: AsyncSession,  # noqa: F811
    assistant: Assistant,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Arq stops a job at its timeout by cancelling it. Cancellation is not an
    Exception, so the source used to stay "processing" forever."""
    import asyncio

    from app.rag.ingest import STOPPED

    class _Slow(_CountingEmbedder):
        async def embed_documents(self, texts: list[str]) -> list[list[float]]:
            await asyncio.sleep(30)
            return []  # pragma: no cover

    monkeypatch.setattr("app.rag.ingest.get_embedder", _Slow)
    source = await _text_source(pg, assistant)
    source_id = source.id
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(ingest_data_source(pg, source_id), timeout=1.0)

    fresh = await pg.get(DataSource, source_id)
    assert fresh is not None
    await pg.refresh(fresh)
    assert fresh.status == DataSourceStatus.error
    assert fresh.error == STOPPED
