"""The regression gate's rule (task 6.2, plan §11.2): a run's metrics
against a committed baseline, with a tolerance.

Pure: metrics in, reasons out. The gate itself is a test
(`tests/test_eval_gate.py`) that runs the fixture suite and fails the
build when this returns anything.

A baseline is the floor, not the target. When a change makes the numbers
better, raise the baseline in the same change, or the gain is unprotected.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.evals.suite import FIXTURES_DIR

#: How far below its baseline a metric may fall before the build fails.
#: One case of a 15-case suite is 0.067, so this allows rounding noise and
#: nothing else.
DEFAULT_TOLERANCE = 0.02

#: The measures the gate watches: (where in a run's metrics, its name).
WATCHED: tuple[tuple[tuple[str, ...], str], ...] = (
    (("pass_rate",), "pass rate"),
    (("retrieval", "recall"), "retrieval recall"),
    (("retrieval", "mrr"), "retrieval MRR"),
    (("retrieval", "ndcg"), "retrieval nDCG"),
)


@dataclass
class Baseline:
    suite: str
    metrics: dict[str, Any]
    tolerance: float = DEFAULT_TOLERANCE
    #: How many cases the numbers were measured over.
    cases: int = 0


def load_baseline(path: str | Path) -> Baseline:
    p = Path(path)
    if not p.is_absolute() and not p.exists():
        p = FIXTURES_DIR / p
    raw = json.loads(p.read_text(encoding="utf-8"))
    return Baseline(
        suite=str(raw["suite"]),
        metrics=dict(raw["metrics"]),
        tolerance=float(raw.get("tolerance", DEFAULT_TOLERANCE)),
        cases=int(raw.get("cases", 0)),
    )


def _at(metrics: Mapping[str, Any], path: tuple[str, ...]) -> float | None:
    value: Any = metrics
    for key in path:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return float(value) if isinstance(value, int | float) else None


def regressions(metrics: Mapping[str, Any], baseline: Baseline) -> list[str]:
    """Why this run fails the gate; empty when it passes.

    - A watched metric fell more than the tolerance below its baseline.
    - A metric the baseline has is missing from the run: a measure that
      silently stopped being taken is a regression, not a pass.
    - A case errored: the turn itself broke.
    - The suite shrank: deleting the failing case is not a fix.
    """
    reasons: list[str] = []
    for path, name in WATCHED:
        floor = _at(baseline.metrics, path)
        if floor is None:
            continue
        now = _at(metrics, path)
        if now is None:
            reasons.append(f"{name} was not measured (baseline {floor:.4f})")
        elif now < floor - baseline.tolerance:
            reasons.append(
                f"{name} fell to {now:.4f} from a baseline of {floor:.4f} "
                f"(allowed drop {baseline.tolerance:.2f})"
            )
    errors = int(metrics.get("errors") or 0)
    if errors:
        reasons.append(f"{errors} case(s) ended in an error")
    ran = int(metrics.get("cases") or 0)
    if ran < baseline.cases:
        reasons.append(f"only {ran} of the baseline's {baseline.cases} cases ran")
    return reasons


__all__ = ["DEFAULT_TOLERANCE", "WATCHED", "Baseline", "load_baseline", "regressions"]
