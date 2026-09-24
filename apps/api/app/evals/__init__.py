"""Evaluation harness. Phase 2 (task 2.11) covers the retrieval half only:
metrics + a labeled fixture set. Answer scoring (Claude-as-judge), the Arq
runner and the CI regression gate are task 6.1.
"""

from app.evals.metrics import (
    RetrievalScores,
    aggregate,
    ndcg_at_k,
    recall_at_k,
    reciprocal_rank,
    score_case,
)
from app.evals.suite import EvalCase, EvalSuite, load_suite

__all__ = [
    "EvalCase",
    "EvalSuite",
    "RetrievalScores",
    "aggregate",
    "load_suite",
    "ndcg_at_k",
    "recall_at_k",
    "reciprocal_rank",
    "score_case",
]
