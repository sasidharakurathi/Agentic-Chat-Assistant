"""Retrieval metrics (task 2.11).

Hand-computed expectations throughout. These three numbers are what every
later retrieval decision gets argued with, so "it returns a plausible float"
is not good enough — each case below states the arithmetic it is pinning.
"""

from __future__ import annotations

import math

import pytest
from app.evals.metrics import aggregate, ndcg_at_k, recall_at_k, reciprocal_rank, score_case
from app.evals.suite import EvalSuite, load_suite, parse_suite

# ── recall@k ─────────────────────────────────────────────────


def test_recall_counts_relevant_found_over_relevant_total() -> None:
    # 2 of the 3 relevant ids are inside the top 4
    assert recall_at_k(["a", "x", "b", "y"], ["a", "b", "c"], 4) == pytest.approx(2 / 3)


def test_recall_respects_the_cutoff() -> None:
    """The whole point of @k: a hit below the cutoff never reached the agent."""
    assert recall_at_k(["x", "y", "a"], ["a"], 2) == 0.0
    assert recall_at_k(["x", "y", "a"], ["a"], 3) == 1.0


def test_recall_is_not_inflated_by_duplicates() -> None:
    assert recall_at_k(["a", "a", "a"], ["a", "b"], 3) == pytest.approx(0.5)


def test_recall_with_no_labels_is_zero_not_a_crash() -> None:
    """One unlabelled case must not take down a whole suite run."""
    assert recall_at_k(["a"], [], 5) == 0.0


def test_recall_with_k_zero_is_zero() -> None:
    assert recall_at_k(["a"], ["a"], 0) == 0.0


# ── MRR ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("retrieved", "expected"),
    [
        (["a", "x", "y"], 1.0),
        (["x", "a", "y"], 0.5),
        (["x", "y", "a"], 1 / 3),
        (["x", "y", "z"], 0.0),
    ],
)
def test_reciprocal_rank_is_one_over_the_first_hit(retrieved: list[str], expected: float) -> None:
    assert reciprocal_rank(retrieved, ["a"]) == pytest.approx(expected)


def test_reciprocal_rank_uses_the_first_hit_only() -> None:
    assert reciprocal_rank(["x", "a", "b"], ["a", "b"]) == pytest.approx(0.5)


# ── nDCG@k ───────────────────────────────────────────────────


def test_ndcg_is_one_when_every_relevant_hit_is_on_top() -> None:
    assert ndcg_at_k(["a", "b", "x"], ["a", "b"], 3) == pytest.approx(1.0)


def test_ndcg_penalises_burying_a_hit() -> None:
    # single relevant at rank 3: DCG = 1/log2(4) = 0.5, IDCG = 1/log2(2) = 1
    assert ndcg_at_k(["x", "y", "a"], ["a"], 3) == pytest.approx(0.5)


def test_ndcg_rewards_finding_more_than_one() -> None:
    """The property recall can't see: both orderings have recall 1.0."""
    both_high = ndcg_at_k(["a", "b", "x", "y"], ["a", "b"], 4)
    one_buried = ndcg_at_k(["a", "x", "y", "b"], ["a", "b"], 4)
    assert both_high == pytest.approx(1.0)
    assert one_buried < both_high


def test_ndcg_idcg_is_capped_by_k() -> None:
    """With 3 labels but k=1, finding the best one is a perfect score — you
    were only ever shown one result."""
    assert ndcg_at_k(["a"], ["a", "b", "c"], 1) == pytest.approx(1.0)


def test_ndcg_matches_hand_computed_dcg_over_idcg() -> None:
    # relevant at ranks 2 and 4 -> DCG = 1/log2(3) + 1/log2(5)
    # ideal = ranks 1 and 2     -> IDCG = 1/log2(2) + 1/log2(3)
    dcg = 1 / math.log2(3) + 1 / math.log2(5)
    idcg = 1 / math.log2(2) + 1 / math.log2(3)
    assert ndcg_at_k(["x", "a", "y", "b"], ["a", "b"], 4) == pytest.approx(dcg / idcg)


def test_ndcg_with_no_labels_is_zero() -> None:
    assert ndcg_at_k(["a"], [], 3) == 0.0


# ── aggregation ──────────────────────────────────────────────


def test_aggregate_macro_averages_and_lists_misses() -> None:
    cases = [
        score_case("hit", ["a"], ["a"], 3),
        score_case("miss", ["z"], ["a"], 3),
    ]
    agg = aggregate(cases, 3)
    assert agg.n == 2
    assert agg.recall_at_k == pytest.approx(0.5)
    assert agg.mrr == pytest.approx(0.5)
    assert agg.misses == ["miss"]


def test_aggregate_weights_every_question_equally() -> None:
    """Macro, not micro: a question with five labels must not drown out one
    with a single label."""
    many = score_case("many", ["a", "b", "c", "d", "e"], ["a", "b", "c", "d", "e"], 5)
    one = score_case("one", ["z"], ["q"], 5)
    assert aggregate([many, one], 5).recall_at_k == pytest.approx(0.5)


def test_aggregate_of_nothing_is_empty_not_a_division_by_zero() -> None:
    agg = aggregate([], 5)
    assert agg.n == 0 and agg.recall_at_k == 0.0


def test_score_case_reports_hit_even_below_the_cutoff() -> None:
    """`hit` answers "did it find it at all", which is a different question
    from recall@k and the one you want when triaging misses."""
    s = score_case("c", ["x", "y", "a"], ["a"], 2)
    assert s.hit is True
    assert s.recall_at_k == 0.0


# ── the labelled fixture set ─────────────────────────────────


def test_the_shipped_suite_loads_and_is_internally_consistent() -> None:
    suite = load_suite("retrieval_support_v1.json")
    assert suite.name == "support-handbook-v1"
    assert len(suite.corpus) >= 10
    assert len(suite.cases) >= 10
    # No duplicate keys, no case pointing at a corpus key that doesn't exist,
    # no unlabelled case — each of which would silently score 0 forever and
    # look like a retrieval regression.
    assert suite.validate() == []


def test_the_suite_has_multi_answer_cases() -> None:
    """Without at least one, nDCG can never say anything recall doesn't."""
    suite = load_suite("retrieval_support_v1.json")
    assert any(len(c.relevant) > 1 for c in suite.cases)


def test_validate_catches_a_label_pointing_at_nothing() -> None:
    suite = parse_suite(
        {
            "name": "broken",
            "corpus": [{"key": "a", "title": "A", "text": "x"}],
            "cases": [{"id": "c1", "input": "q", "relevant": ["nope"]}],
        }
    )
    assert any("unknown corpus key" in p for p in suite.validate())


def test_validate_catches_duplicate_case_ids_and_missing_labels() -> None:
    suite = EvalSuite(
        name="broken",
        corpus=[],
        cases=parse_suite(
            {
                "cases": [
                    {"id": "dup", "input": "a", "relevant": []},
                    {"id": "dup", "input": "b", "relevant": []},
                ]
            }
        ).cases,
    )
    problems = suite.validate()
    assert any("duplicate case id" in p for p in problems)
    assert any("no labels" in p for p in problems)
