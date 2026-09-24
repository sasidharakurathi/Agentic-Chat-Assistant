"""The labelled suite run through real retrieval (task 2.11).

Integration tier: real Postgres, real local embedder, real cross-encoder
reranker. That is the point — this is the first thing in the project that
answers "is retrieval any *good*", as opposed to "does retrieval run", and a
mocked embedder could not answer it at all.

The thresholds are floors, not targets. They exist so a change that quietly
halves recall fails the build; they are deliberately below what the pipeline
currently scores, so ordinary noise (a reranker tie, a chunker tweak) doesn't
turn this into a flaky test.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import pytest
from app.evals import load_suite
from app.evals.retrieval import format_report, run_retrieval_suite
from app.models.assistant import Assistant, AssistantStatus
from app.models.organization import Organization
from app.models.rag import Chunk, DataSource, DataSourceStatus, DataSourceType, Document
from app.rag.ingest import ingest_data_source
from app.schemas.assistant_config import RagRetrieval
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration

POSTGRES_URL = os.environ.get(
    "INTEGRATION_DATABASE_URL", "postgresql+asyncpg://app:app@localhost:45432/app"
)

SUITE = "retrieval_support_v1.json"


@pytest.fixture
async def pg() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(POSTGRES_URL)
    try:
        async with engine.connect():
            pass
    except Exception as exc:  # pragma: no cover - environment-dependent
        await engine.dispose()
        pytest.skip(f"Postgres not reachable at {POSTGRES_URL}: {exc}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with sessionmaker() as session:
        yield session
    await engine.dispose()


@pytest.fixture
async def indexed_corpus(pg: AsyncSession):
    """Ingest the suite's corpus for real and return (assistant, key->chunk ids).

    One data source per corpus passage, so a label key maps cleanly onto the
    chunks that passage produced — the bridge `run_retrieval_suite` needs
    between author-written keys and generated chunk ids.
    """
    suite = load_suite(SUITE)
    assert suite.validate() == []

    org = Organization(name="Eval Org", slug=f"eval-{uuid.uuid4().hex[:8]}")
    pg.add(org)
    await pg.flush()
    assistant = Assistant(
        org_id=org.id, name="Eval Bot", slug="eval-bot", status=AssistantStatus.draft
    )
    pg.add(assistant)
    await pg.commit()

    try:
        key_to_chunks: dict[str, set[uuid.UUID]] = {}
        for doc in suite.corpus:
            source = DataSource(
                assistant_id=assistant.id,
                org_id=org.id,
                type=DataSourceType.text,
                name=f"{doc.title} / {doc.key}",
                status=DataSourceStatus.pending,
                config={"text": doc.text},
            )
            pg.add(source)
            await pg.commit()
            await ingest_data_source(pg, source.id)

            rows = (
                await pg.execute(
                    select(Chunk.id)
                    .join(Document, Document.id == Chunk.document_id)
                    .where(Document.data_source_id == source.id)
                )
            ).scalars()
            key_to_chunks[doc.key] = set(rows)
            assert key_to_chunks[doc.key], f"{doc.key} produced no chunks"

        yield assistant, suite, key_to_chunks
    finally:
        fresh = await pg.get(Organization, org.id)
        if fresh is not None:
            await pg.delete(fresh)
            await pg.commit()


EVAL_CONFIG = RagRetrieval(
    # rerank_top_n must not exceed the candidate pool, and the corpus is only
    # 12 passages, so the pool is capped down to match.
    top_k_dense=12,
    top_k_sparse=12,
    rerank_top_n=5,
    # Thresholding is a separate decision from ranking quality; leaving it on
    # would make this measure `min_score` tuning rather than the retriever.
    min_score=0.0,
)


async def test_the_shipped_suite_scores_above_its_floor(
    pg: AsyncSession, indexed_corpus: tuple
) -> None:
    """Scored at **k=1**, deliberately.

    On a 12-passage corpus, recall@5 hands back 42% of everything indexed, so
    it sits at 1.000 and would keep sitting there through a substantial
    regression — a number with no headroom tests nothing. recall@1 and MRR
    ask the question that still has room to fail: is the *first* result the
    right one? The k=5 figures are reported below for context, not asserted
    on.
    """
    assistant, suite, keys = indexed_corpus
    at1 = await run_retrieval_suite(
        pg,
        assistant_id=assistant.id,
        suite=suite,
        key_to_chunk_ids=keys,
        config=EVAL_CONFIG,
        k=1,
    )
    at5 = await run_retrieval_suite(
        pg, assistant_id=assistant.id, suite=suite, key_to_chunk_ids=keys, config=EVAL_CONFIG
    )
    print("\n" + format_report(at1, suite.name + " @1"))
    print(format_report(at5, suite.name + " (what the agent is shown)"))

    assert at1.n == len(suite.cases)
    # Floors with real headroom: the pipeline currently scores recall@1 0.917
    # (capped there by the two multi-answer cases, which cannot exceed 0.5 at
    # k=1) and MRR 1.000.
    assert at1.mrr >= 0.85, f"the top result stopped being right: MRR {at1.mrr}"
    assert at1.recall_at_k >= 0.75, f"recall@1 regressed: {at1.recall_at_k}"
    assert at5.recall_at_k >= 0.90, f"recall@5 regressed: {at5.recall_at_k}"


async def test_paraphrased_questions_are_found_at_all(
    pg: AsyncSession, indexed_corpus: tuple
) -> None:
    """The cases with no keyword overlap with their answer — if these missed,
    the dense half of hybrid search would be contributing nothing."""
    assistant, suite, keys = indexed_corpus
    paraphrased = {"q_refund_window", "q_warranty_length", "q_dropped_it"}
    scores = await run_retrieval_suite(
        pg,
        assistant_id=assistant.id,
        suite=suite,
        key_to_chunk_ids=keys,
        config=EVAL_CONFIG,
    )
    missed = paraphrased & set(scores.misses)
    assert not missed, f"semantic retrieval failed on: {missed}"


async def test_a_tighter_cutoff_cannot_score_higher(
    pg: AsyncSession, indexed_corpus: tuple
) -> None:
    """Sanity property of the whole harness: recall@1 <= recall@5 always. If
    this ever failed, the metrics or the cutoff plumbing would be wrong, not
    the retriever."""
    assistant, suite, keys = indexed_corpus
    cfg = RagRetrieval(top_k_dense=12, top_k_sparse=12, rerank_top_n=5, min_score=0.0)
    at1 = await run_retrieval_suite(
        pg, assistant_id=assistant.id, suite=suite, key_to_chunk_ids=keys, config=cfg, k=1
    )
    at5 = await run_retrieval_suite(
        pg, assistant_id=assistant.id, suite=suite, key_to_chunk_ids=keys, config=cfg, k=5
    )
    print("\n" + format_report(at1, suite.name + " @1"))
    print(format_report(at5, suite.name + " @5"))
    assert at1.recall_at_k <= at5.recall_at_k
    assert at1.ndcg_at_k <= at5.ndcg_at_k + 1e-9
