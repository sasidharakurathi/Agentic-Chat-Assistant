"""Eval suites, their cases, runs and per-case results (task 6.1, plan §2.6).

A suite belongs to one assistant. A run takes every case through the real
chat path against one chosen version (or the draft), so what it measures is
what a user would have got. Each case's turn is an ordinary conversation,
hidden from the chat list (`conversations.eval_run_id`), which is what gives
a result its run details and trace for free.

A result keeps its own copy of the case's input and expectations: cases get
edited and deleted, and a result that changed meaning afterwards would make
two runs impossible to compare.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import JSONB, TZDateTime


class EvalRunStatus(enum.StrEnum):
    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"
    cancelled = "cancelled"

    @property
    def active(self) -> bool:
        return self in (EvalRunStatus.queued, EvalRunStatus.running)


class EvalSuite(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "eval_suites"

    assistant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assistants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    #: `schemas.evals.EvalSuiteConfig`: whether the judge runs, and the pass mark.
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class EvalCase(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "eval_cases"

    suite_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("eval_suites.id", ondelete="CASCADE"), nullable=False, index=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: Order in the suite; results are listed the same way.
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input: Mapped[str] = mapped_column(Text, nullable=False)
    #: `schemas.evals.EvalExpected`: a reference answer and the free checks.
    expected: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    #: `schemas.evals.EvalLabels`: what retrieval should have found.
    labels: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    #: The author's own notes and tags ("metadata" is taken by SQLAlchemy).
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)


class EvalRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "eval_runs"

    suite_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("eval_suites.id", ondelete="CASCADE"), nullable=False, index=True
    )
    assistant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assistants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: Null = the draft as it was when the run started.
    assistant_version_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    #: Kept beside the id so a run still says "v7" once that version is gone.
    version_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[EvalRunStatus] = mapped_column(
        SAEnum(EvalRunStatus, name="eval_run_status", native_enum=False, length=20),
        nullable=False,
        default=EvalRunStatus.queued,
    )
    #: `evals.aggregate.summarize`: pass rate, judge means, retrieval scores.
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    cost_usd: Mapped[float] = mapped_column(Numeric(12, 6), nullable=False, default=0)
    total_cases: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    done_cases: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Why a failed run stopped, in words a builder can act on.
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)


class EvalCaseResult(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "eval_case_results"

    eval_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("eval_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: Which case this was, while it still exists (for comparing two runs).
    eval_case_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("eval_cases.id", ondelete="SET NULL"), nullable=True, index=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # The case as it was when it ran.
    input: Mapped[str] = mapped_column(Text, nullable=False)
    expected: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    output: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: `{"checks": [...], "judge": {...} | None, "retrieval": {...} | None}`.
    scores: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: Set when the turn itself failed (a model error, a spent budget).
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    cost_usd: Mapped[float] = mapped_column(Numeric(12, 6), nullable=False, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Where to look: the hidden conversation, its run row and its trace.
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


__all__ = ["EvalCase", "EvalCaseResult", "EvalRun", "EvalRunStatus", "EvalSuite"]
