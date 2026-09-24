"""``rrf_fuse`` is pure — no DB, fast. The rest of retrieve.py needs real
Postgres + a real embedder/reranker; see test_rag_retrieve.py
(``-m integration``)."""

from __future__ import annotations

import uuid

from app.rag.retrieve import rrf_fuse
from app.rag.vectorstore import ScoredChunk


def _chunk(n: int) -> ScoredChunk:
    return ScoredChunk(
        id=uuid.UUID(int=n), document_id=uuid.uuid4(), content=f"chunk {n}", score=0.0
    )


def test_a_chunk_found_by_both_lists_outranks_one_found_by_only_one() -> None:
    a, b, c = _chunk(1), _chunk(2), _chunk(3)
    dense = [a, b]  # a is rank 1 in dense, b is rank 2
    sparse = [b, c]  # b is rank 1 in sparse, c is rank 2
    fused = rrf_fuse(dense, sparse, rrf_k=60)
    # b appears in both lists (ranks 2 and 1) -> highest combined score
    assert fused[0].id == b.id


def test_order_within_a_single_list_is_preserved_when_only_one_list_has_hits() -> None:
    a, b, c = _chunk(1), _chunk(2), _chunk(3)
    fused = rrf_fuse([a, b, c], [], rrf_k=60)
    assert [c.id for c in fused] == [a.id, b.id, c.id]


def test_empty_inputs_produce_no_results() -> None:
    assert rrf_fuse([], [], rrf_k=60) == []


def test_disjoint_lists_interleave_by_rank() -> None:
    a, b = _chunk(1), _chunk(2)
    x, y = _chunk(3), _chunk(4)
    # a and x are both rank-1 in their own list -> tied RRF score -> both
    # ahead of the rank-2 entries, which are also tied with each other.
    fused = rrf_fuse([a, b], [x, y], rrf_k=60)
    assert {c.id for c in fused[:2]} == {a.id, x.id}
    assert {c.id for c in fused[2:]} == {b.id, y.id}


def test_lower_rrf_k_makes_top_ranks_matter_more() -> None:
    a, b = _chunk(1), _chunk(2)
    # a is rank 1 in both lists; b is rank 5 in both lists.
    filler = [_chunk(100 + i) for i in range(3)]
    dense = [a, *filler, b]
    sparse = [a, *filler, b]
    fused_small_k = rrf_fuse(dense, sparse, rrf_k=1)
    fused_large_k = rrf_fuse(dense, sparse, rrf_k=1000)
    assert fused_small_k[0].id == a.id
    assert fused_large_k[0].id == a.id

    # with a small k, rank 1 dominates far more than with a huge k (which
    # flattens everyone toward the same score) — the gap should shrink.
    def score_map(rrf_k: int) -> dict[uuid.UUID, float]:
        scores: dict[uuid.UUID, float] = {}
        for lst in (dense, sparse):
            for rank, ch in enumerate(lst, start=1):
                scores[ch.id] = scores.get(ch.id, 0.0) + 1.0 / (rrf_k + rank)
        return scores

    small = score_map(1)
    large = score_map(1000)
    assert (small[a.id] - small[b.id]) > (large[a.id] - large[b.id])
