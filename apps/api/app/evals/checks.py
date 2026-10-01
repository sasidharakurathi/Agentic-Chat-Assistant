"""The free checks on an answer (task 6.1): does it say what it must, avoid
what it mustn't, use the tools it should, and cite a source.

Pure functions of the answer and what the turn did: no model, no database.
They cost nothing, give the same verdict every time, and are what the CI
regression gate (task 6.2) runs on, where there is no judge.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from typing import Any

from app.schemas.evals import EvalExpected


@dataclass
class Check:
    #: "contains", "not_contains", "tool" or "cites".
    kind: str
    #: What was looked for ("" for cites).
    target: str
    passed: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _fold(text: str) -> str:
    """Case and whitespace don't decide whether an answer said something."""
    return " ".join(text.casefold().split())


def tool_called(wanted: str, called: Iterable[str]) -> bool:
    """`sql_query` matches `mcp__caps__sql_query`: a builder writes the short
    name, the turn records the full one. A full name matches only itself."""
    want = wanted.strip()
    return any(name == want or name.rsplit("__", 1)[-1] == want for name in called)


def run_checks(
    expected: EvalExpected, *, answer: str, tools: Iterable[str], citations: int
) -> list[Check]:
    """One `Check` per thing the case asked for, in a stable order."""
    folded = _fold(answer)
    called = list(tools)
    checks = [Check("contains", phrase, _fold(phrase) in folded) for phrase in expected.contains]
    checks += [
        Check("not_contains", phrase, _fold(phrase) not in folded)
        for phrase in expected.not_contains
    ]
    checks += [Check("tool", name, tool_called(name, called)) for name in expected.tools]
    if expected.cites:
        checks.append(Check("cites", "", citations > 0))
    return checks


__all__ = ["Check", "run_checks", "tool_called"]
