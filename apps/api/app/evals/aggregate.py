"""A case's verdict, and a run's metrics from its cases (task 6.1).

Pure functions over the stored `scores` of each result, so a run's numbers
can always be rebuilt from its rows and are exactly testable.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from app.evals.judge import DIMENSIONS


def verdict(scores: Mapping[str, Any], error: str | None) -> bool:
    """Did the case pass? The turn must have finished, every check the case
    asked for must hold, and, when the judge graded it, the judge must
    agree. A case that asked for nothing passes by finishing."""
    if error:
        return False
    if any(not c.get("passed") for c in scores.get("checks") or []):
        return False
    judged = scores.get("judge")
    return not (isinstance(judged, Mapping) and judged.get("passed") is False)


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def _judge_metrics(judged: list[Mapping[str, Any]]) -> dict[str, Any] | None:
    if not judged:
        return None
    out: dict[str, Any] = {"judged": len(judged)}
    for name in DIMENSIONS:
        scores = [float(j[name]["score"]) for j in judged if isinstance(j.get(name), Mapping)]
        out[name] = _mean(scores)
    return out


def _retrieval_metrics(scored: list[Mapping[str, Any]]) -> dict[str, Any] | None:
    if not scored:
        return None
    return {
        "cases": len(scored),
        "recall": _mean([float(s["recall"]) for s in scored]),
        "mrr": _mean([float(s["reciprocal_rank"]) for s in scored]),
        "ndcg": _mean([float(s["ndcg"]) for s in scored]),
        # Questions where nothing relevant surfaced at all.
        "missed": sum(1 for s in scored if not s.get("hit")),
    }


def summarize(results: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """`results`: each case's `{"scores", "passed", "error"}`. Averages are
    over the cases a measure applied to, not over the whole suite: a suite
    with two labelled questions reports retrieval over two."""
    rows = list(results)
    checks = [c for r in rows for c in (r["scores"].get("checks") or [])]
    judged = [r["scores"]["judge"] for r in rows if isinstance(r["scores"].get("judge"), Mapping)]
    graded = [j for j in judged if "error" not in j]
    retrieved = [
        r["scores"]["retrieval"]
        for r in rows
        # One that couldn't be scored carries only an "error".
        if isinstance(r["scores"].get("retrieval"), Mapping)
        and "recall" in r["scores"]["retrieval"]
    ]
    passed = sum(1 for r in rows if r["passed"])
    return {
        "cases": len(rows),
        "passed": passed,
        "failed": len(rows) - passed,
        "errors": sum(1 for r in rows if r.get("error")),
        "pass_rate": round(passed / len(rows), 4) if rows else None,
        "checks": {"total": len(checks), "passed": sum(1 for c in checks if c.get("passed"))},
        "judge": _judge_metrics(graded),
        "judge_errors": len(judged) - len(graded),
        "retrieval": _retrieval_metrics(retrieved),
    }


__all__ = ["summarize", "verdict"]
