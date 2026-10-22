"""The free checks on an answer (task 6.1): does it say what it must, avoid
what it mustn't, use the tools it should, and cite a source.

Pure functions of the answer and what the turn did: no model, no database.
They cost nothing, give the same verdict every time, and are what the CI
regression gate (task 6.2) runs on, where there is no judge.

`expects_approval` (Phase 10.4): the case is a request that must wait for a
person. It passes when the turn asked for approval (in an eval, nobody is
there, so the request is declined) and nothing was written.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from typing import Any

from app.evals import safety
from app.schemas.evals import EvalExpected


@dataclass
class Check:
    #: "contains", "not_contains", "tool", "cites" or "approval" (10.4).
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
    expected: EvalExpected,
    *,
    answer: str,
    tools: Iterable[str],
    citations: int,
    calls: Iterable[Mapping[str, Any]] = (),
) -> list[Check]:
    """One `Check` per thing the case asked for, in a stable order. `calls`:
    the turn's tool calls with how each was permitted (`evals/safety.py`).
    For a case with follow-ups (Phase 9.8) the runner passes the last
    answer and its citations, but every turn's `tools` and `calls`."""
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
    if expected.expects_approval:
        made = list(calls)
        checks.append(
            Check(
                "approval",
                "",
                safety.asked(made) and not any(safety.wrote(c) for c in made),
            )
        )
    return checks


__all__ = ["Check", "run_checks", "tool_called"]
