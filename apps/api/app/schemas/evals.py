"""Eval suites, cases, runs and results over the API (task 6.1)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import Field, field_validator

from app.schemas.common import ApiModel

#: A suite this size already takes minutes and real money on a real model.
MAX_CASES = 200
MAX_INPUT_CHARS = 8_000


def _clean(items: list[str]) -> list[str]:
    """Trimmed, non-empty, no repeats, order kept."""
    seen: dict[str, None] = {}
    for item in items:
        text = item.strip()
        if text:
            seen.setdefault(text, None)
    return list(seen)


class EvalSuiteConfig(ApiModel):
    """How a suite is scored."""

    #: Ask the judge model to grade each answer (only on the real model).
    judge: bool = True
    #: A judged answer passes when every rubric score is at least this.
    pass_score: int = Field(default=3, ge=1, le=5)


class EvalExpected(ApiModel):
    """What a good answer looks like. Everything is optional: a case with
    nothing here passes whenever the turn finishes."""

    #: A reference answer, for the judge to compare against.
    reference: str | None = Field(default=None, max_length=MAX_INPUT_CHARS)
    #: The answer must include each of these (case-insensitive).
    contains: list[str] = Field(default_factory=list, max_length=20)
    #: The answer must include none of these.
    not_contains: list[str] = Field(default_factory=list, max_length=20)
    #: Tools the turn must have called ("sql_query", "kb_search", ...).
    tools: list[str] = Field(default_factory=list, max_length=10)
    #: The answer must cite at least one source.
    cites: bool = False
    #: The right answer is to decline (tells the judge what to expect).
    refuses: bool = False

    @field_validator("contains", "not_contains", "tools")
    @classmethod
    def _tidy(cls, v: list[str]) -> list[str]:
        return _clean(v)


class EvalLabels(ApiModel):
    """What retrieval should find for this question. Sources are named by
    their title or id: a builder knows their documents, not chunk ids."""

    relevant_sources: list[str] = Field(default_factory=list, max_length=50)
    relevant_chunk_ids: list[str] = Field(default_factory=list, max_length=200)

    @field_validator("relevant_sources", "relevant_chunk_ids")
    @classmethod
    def _tidy(cls, v: list[str]) -> list[str]:
        return _clean(v)

    @property
    def any(self) -> bool:
        return bool(self.relevant_sources or self.relevant_chunk_ids)


class EvalCaseIn(ApiModel):
    input: str = Field(min_length=1, max_length=MAX_INPUT_CHARS)
    expected: EvalExpected = Field(default_factory=EvalExpected)
    labels: EvalLabels = Field(default_factory=EvalLabels)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("input")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("A case needs a question")
        return v.strip()


class EvalCaseOut(ApiModel):
    id: uuid.UUID
    position: int
    input: str
    expected: EvalExpected
    labels: EvalLabels
    metadata: dict[str, Any]


class EvalCasesBulkIn(ApiModel):
    cases: list[EvalCaseIn] = Field(min_length=1, max_length=MAX_CASES)
    #: "append" adds to the suite; "replace" swaps every case for these.
    mode: Literal["append", "replace"] = "append"


class EvalSuiteIn(ApiModel):
    name: str = Field(min_length=1, max_length=120)
    config: EvalSuiteConfig = Field(default_factory=EvalSuiteConfig)


class EvalSuitePatch(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    config: EvalSuiteConfig | None = None


class EvalRunSummary(ApiModel):
    id: uuid.UUID
    suite_id: uuid.UUID
    status: Literal["queued", "running", "done", "failed", "cancelled"]
    #: Null = the draft.
    assistant_version_id: uuid.UUID | None
    version_number: int | None
    metrics: dict[str, Any]
    cost_usd: float
    total_cases: int
    done_cases: int
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class EvalSuiteOut(ApiModel):
    id: uuid.UUID
    assistant_id: uuid.UUID
    name: str
    config: EvalSuiteConfig
    case_count: int
    last_run: EvalRunSummary | None
    created_at: datetime


class EvalSuiteDetail(EvalSuiteOut):
    cases: list[EvalCaseOut]
    #: Whether the judge would run right now (it needs the real model).
    judge_available: bool


class EvalSuitesOut(ApiModel):
    suites: list[EvalSuiteOut]
    can_edit: bool


class EvalRunIn(ApiModel):
    #: The published version to test; null tests the draft.
    assistant_version_id: uuid.UUID | None = None


class EvalCaseResultOut(ApiModel):
    id: uuid.UUID
    eval_case_id: uuid.UUID | None
    position: int
    input: str
    expected: EvalExpected
    output: str
    scores: dict[str, Any]
    passed: bool
    error: str | None
    cost_usd: float
    duration_ms: int
    conversation_id: uuid.UUID | None
    run_id: uuid.UUID | None
    trace_id: str | None


class EvalRunDetail(EvalRunSummary):
    results: list[EvalCaseResultOut]


class EvalRunsOut(ApiModel):
    runs: list[EvalRunSummary]


__all__ = [
    "MAX_CASES",
    "EvalCaseIn",
    "EvalCaseOut",
    "EvalCaseResultOut",
    "EvalCasesBulkIn",
    "EvalExpected",
    "EvalLabels",
    "EvalRunDetail",
    "EvalRunIn",
    "EvalRunSummary",
    "EvalRunsOut",
    "EvalSuiteConfig",
    "EvalSuiteDetail",
    "EvalSuiteIn",
    "EvalSuiteOut",
    "EvalSuitePatch",
    "EvalSuitesOut",
]
