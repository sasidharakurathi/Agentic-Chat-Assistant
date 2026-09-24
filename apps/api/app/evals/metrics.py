"""Retrieval metrics: recall@k, MRR and nDCG@k (plan §11.2).

Pure functions over ranked id lists — no DB, no embedder, no config. That is
deliberate: these are the numbers every later decision about retrieval gets
argued with (is hybrid search earning its keep? does contextual retrieval
actually help? did raising `rerank_top_n` do anything?), so they need to be
fast, exactly testable, and impossible to get subtly wrong without a test
noticing.

All three assume **binary relevance** — a chunk either answers the question
or it doesn't. Graded relevance is what nDCG is really built for, but the
labelled set is hand-written and "somewhat relevant" is not a judgement two
people would make the same way twice.

Why all three, when they look similar:

* **recall@k** — did we *find* the answer at all? The one that matters most
  here, because a passage that never reaches the reranker can never be cited.
* **MRR** — how far down was the first good hit? Catches "technically found
  it, at rank 9".
* **nDCG@k** — rewards getting *several* relevant passages high up, not just
  the first one. The only one of the three that notices when a question has
  three good sources and we surfaced one.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass


def _top_k(retrieved: Sequence[str], k: int) -> Sequence[str]:
    if k <= 0:
        return []
    return retrieved[:k]


def recall_at_k(retrieved: Sequence[str], relevant: Iterable[str], k: int) -> float:
    """Fraction of the relevant chunks that appear in the top k.

    Undefined with no labels; returns 0.0 rather than raising, so one
    unlabelled case can't take down a suite run.
    """
    gold = set(relevant)
    if not gold:
        return 0.0
    found = sum(1 for cid in set(_top_k(retrieved, k)) if cid in gold)
    return found / len(gold)


def reciprocal_rank(retrieved: Sequence[str], relevant: Iterable[str]) -> float:
    """1 / (rank of the first relevant hit), 1-indexed; 0.0 if none."""
    gold = set(relevant)
    for i, cid in enumerate(retrieved, start=1):
        if cid in gold:
            return 1.0 / i
    return 0.0


def ndcg_at_k(retrieved: Sequence[str], relevant: Iterable[str], k: int) -> float:
    """Normalised discounted cumulative gain, binary gains.

    DCG sums 1/log2(rank+1) over the relevant hits in the top k; IDCG is the
    same sum for the best possible ordering (every relevant chunk packed into
    the top positions), which is what makes the result comparable across
    questions with different numbers of right answers.
    """
    gold = set(relevant)
    if not gold or k <= 0:
        return 0.0
    dcg = sum(
        1.0 / math.log2(i + 1) for i, cid in enumerate(_top_k(retrieved, k), start=1) if cid in gold
    )
    ideal_hits = min(len(gold), k)
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, ideal_hits + 1))
    return dcg / idcg if idcg else 0.0


@dataclass
class CaseScore:
    case_id: str
    recall_at_k: float
    reciprocal_rank: float
    ndcg_at_k: float
    retrieved: int
    relevant: int
    hit: bool


@dataclass
class RetrievalScores:
    """Macro-averaged over cases — every question counts the same, regardless
    of how many relevant passages it happens to have."""

    k: int
    cases: list[CaseScore]
    recall_at_k: float = 0.0
    mrr: float = 0.0
    ndcg_at_k: float = 0.0

    @property
    def n(self) -> int:
        return len(self.cases)

    @property
    def misses(self) -> list[str]:
        """Cases where nothing relevant surfaced at all — the list worth
        reading before any of the averages."""
        return [c.case_id for c in self.cases if not c.hit]


def score_case(
    case_id: str, retrieved: Sequence[str], relevant: Iterable[str], k: int
) -> CaseScore:
    gold = set(relevant)
    return CaseScore(
        case_id=case_id,
        recall_at_k=recall_at_k(retrieved, gold, k),
        reciprocal_rank=reciprocal_rank(retrieved, gold),
        ndcg_at_k=ndcg_at_k(retrieved, gold, k),
        retrieved=len(retrieved),
        relevant=len(gold),
        hit=any(cid in gold for cid in retrieved),
    )


def aggregate(cases: list[CaseScore], k: int) -> RetrievalScores:
    if not cases:
        return RetrievalScores(k=k, cases=[])
    n = len(cases)
    return RetrievalScores(
        k=k,
        cases=cases,
        recall_at_k=round(sum(c.recall_at_k for c in cases) / n, 4),
        mrr=round(sum(c.reciprocal_rank for c in cases) / n, 4),
        ndcg_at_k=round(sum(c.ndcg_at_k for c in cases) / n, 4),
    )


__all__ = [
    "CaseScore",
    "RetrievalScores",
    "aggregate",
    "ndcg_at_k",
    "recall_at_k",
    "reciprocal_rank",
    "score_case",
]
