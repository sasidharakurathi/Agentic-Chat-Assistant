"""What a reviewer sees beside Approve (Phase 10.4): the eval scorecard,
the "no unapproved write" safety line, and a cost forecast."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field

from app.schemas.common import ApiModel


class SuiteScoreOut(ApiModel):
    """One suite's latest finished run for this version."""

    suite_id: uuid.UUID
    suite_name: str
    #: None when the suite hasn't been run on this version.
    run_id: uuid.UUID | None = None
    #: "version": run on this version. "draft": run on the draft after this
    #: version was submitted, so it may differ if the draft changed since.
    ran_on: Literal["version", "draft"] | None = None
    finished_at: datetime | None = None
    cases: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    pass_rate: float | None = None
    #: The same suite on the version it would replace, when that was run.
    previous_pass_rate: float | None = None
    #: Why it would fail the regression gate against that run (task 6.2).
    regressions: list[str] = Field(default_factory=list)


class FlaggedCallOut(ApiModel):
    """A write that ran without anyone approving it: should never happen."""

    tool: str
    run_id: uuid.UUID
    conversation_id: uuid.UUID


class SafetyOut(ApiModel):
    #: Every tool call the runs above made.
    checked_calls: int
    #: Writes that ran without approval. Expected: 0.
    writes_without_approval: int
    #: Calls that asked a person (in an eval, declined: nobody is there).
    approval_requests: int
    #: Cases that expect an approval to be asked for, and how many did.
    approval_cases: int
    approval_cases_passed: int
    #: True when nothing was written without approval.
    ok: bool
    flagged: list[FlaggedCallOut] = Field(default_factory=list)


class CostForecastOut(ApiModel):
    """An estimate, not a quote."""

    #: Eval conversations the average is taken over.
    conversations_measured: int
    #: Their average cost, the turn only (not the judge).
    avg_cost_usd: float | None = None
    #: The expected conversations a month, as the reviewer entered it.
    per_month: int
    mid_usd: float | None = None
    #: ±30% around the middle.
    low_usd: float | None = None
    high_usd: float | None = None
    note: str


class VersionEvidenceOut(ApiModel):
    version_number: int
    #: The version the scorecard is compared with: the live one, or the
    #: approved one before this. None when there is none.
    compared_with: int | None = None
    scorecard: list[SuiteScoreOut]
    safety: SafetyOut
    cost: CostForecastOut
    #: Plain sentences about what the evidence does and doesn't cover.
    notes: list[str] = Field(default_factory=list)


__all__ = [
    "CostForecastOut",
    "FlaggedCallOut",
    "SafetyOut",
    "SuiteScoreOut",
    "VersionEvidenceOut",
]
