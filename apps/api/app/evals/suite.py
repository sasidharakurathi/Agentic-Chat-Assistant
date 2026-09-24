"""Eval suite format: a corpus plus labelled questions (plan §11.2).

The awkward part of a retrieval suite is that labels have to name *chunks*,
but chunk ids don't exist until the corpus has been ingested — and change
every time it is reindexed. So the fixture labels by a stable author-written
`key` on each corpus passage, and the harness resolves keys to real chunk ids
after ingestion. That keeps the labelled set a hand-editable file rather than
something that has to be regenerated whenever the chunker changes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@dataclass
class CorpusDoc:
    """One passage of the stub corpus. `key` is what cases refer to."""

    key: str
    title: str
    text: str


@dataclass
class EvalCase:
    id: str
    input: str
    #: corpus keys that genuinely answer this question
    relevant: list[str] = field(default_factory=list)
    reference: str | None = None
    notes: str | None = None


@dataclass
class EvalSuite:
    name: str
    corpus: list[CorpusDoc]
    cases: list[EvalCase]

    def validate(self) -> list[str]:
        """Problems that make a suite meaningless rather than merely small.

        Worth running before a suite is used: a case pointing at a corpus key
        that doesn't exist scores 0 forever and looks like a retrieval bug.
        """
        problems: list[str] = []
        keys = {d.key for d in self.corpus}
        if len(keys) != len(self.corpus):
            problems.append("corpus has duplicate keys")
        seen: set[str] = set()
        for case in self.cases:
            if case.id in seen:
                problems.append(f"duplicate case id {case.id!r}")
            seen.add(case.id)
            if not case.relevant:
                problems.append(f"case {case.id!r} has no labels")
            for key in case.relevant:
                if key not in keys:
                    problems.append(f"case {case.id!r} references unknown corpus key {key!r}")
        return problems


def parse_suite(raw: dict[str, Any]) -> EvalSuite:
    return EvalSuite(
        name=str(raw.get("name", "unnamed")),
        corpus=[
            CorpusDoc(key=str(d["key"]), title=str(d.get("title", d["key"])), text=str(d["text"]))
            for d in raw.get("corpus", [])
        ],
        cases=[
            EvalCase(
                id=str(c["id"]),
                input=str(c["input"]),
                relevant=[str(k) for k in c.get("relevant", [])],
                reference=c.get("reference"),
                notes=c.get("notes"),
            )
            for c in raw.get("cases", [])
        ],
    )


def load_suite(path: str | Path) -> EvalSuite:
    p = Path(path)
    if not p.is_absolute() and not p.exists():
        p = FIXTURES_DIR / p
    return parse_suite(json.loads(p.read_text(encoding="utf-8")))


__all__ = [
    "FIXTURES_DIR",
    "CorpusDoc",
    "EvalCase",
    "EvalSuite",
    "load_suite",
    "parse_suite",
]
