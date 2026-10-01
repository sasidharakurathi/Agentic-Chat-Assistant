"""Sample assistants, shipped as graphs (task 6.7).

Each file in this folder is one assistant someone can start from: the
canvas graph, and optionally a few documents for its knowledge base and an
eval suite to run against it. The graph is the source of truth, as it is
for any assistant (ADR 0004): the config is compiled from it when the file
is loaded, by the same compiler the canvas uses.

A sample may only use what every install has: no database connection, no
MCP server, nothing that needs a credential. `tests/test_samples.py` holds
each file to that, and to compiling and validating cleanly.

To add one: build it on the canvas, copy the draft graph
(`GET /assistants/{id}/draft-graph`) into a new file here next to a name,
a description and what it needs, and the tests will say what is missing.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pydantic import Field

from app.graph.compile import compile_graph
from app.graph.nodes import Graph
from app.schemas.assistant_config import AssistantConfig
from app.schemas.common import ApiModel
from app.schemas.evals import EvalCaseIn, EvalSuiteConfig

FOLDER = Path(__file__).parent


class SampleDocument(ApiModel):
    """A short text for the sample's knowledge base, added as a pasted-text
    source when the assistant is created."""

    name: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=20_000)


class SampleEvalSuite(ApiModel):
    name: str = Field(min_length=1, max_length=120)
    config: EvalSuiteConfig = Field(default_factory=EvalSuiteConfig)
    cases: list[EvalCaseIn] = Field(min_length=1, max_length=30)


class Sample(ApiModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{2,40}$")
    #: Where it sits in the gallery: lowest first.
    order: int = Field(default=100, ge=0)
    name: str = Field(min_length=1, max_length=80)
    #: One or two sentences: what it is for.
    description: str = Field(min_length=1, max_length=400)
    #: What has to be true for it to work as described, in plain words.
    #: Empty when it works as it is.
    needs: list[str] = Field(default_factory=list, max_length=5)
    #: Messages worth sending first.
    try_asking: list[str] = Field(default_factory=list, max_length=6)
    graph: Graph
    documents: list[SampleDocument] = Field(default_factory=list, max_length=10)
    eval_suite: SampleEvalSuite | None = None

    def config(self) -> AssistantConfig:
        return compile_graph(self.graph)


@lru_cache
def all_samples() -> tuple[Sample, ...]:
    """Every sample, in gallery order. A file that does not load is a bug in
    the file, so it raises: the tests load them all."""
    found = [
        Sample.model_validate(json.loads(path.read_text(encoding="utf-8")))
        for path in sorted(FOLDER.glob("*.json"))
    ]
    return tuple(sorted(found, key=lambda s: (s.order, s.id)))


def get(sample_id: str) -> Sample | None:
    return next((s for s in all_samples() if s.id == sample_id), None)


__all__ = ["Sample", "SampleDocument", "SampleEvalSuite", "all_samples", "get"]
