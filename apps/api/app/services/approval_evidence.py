"""Approve on evidence (Phase 10.4): what a reviewer reads beside Approve.

Three things, all from eval runs that already happened; nothing here runs
a model or spends:

1. **Scorecard.** For each of the assistant's suites, its latest finished
   run on this version. A run records the version it ran against, so this
   is exact; when a suite was only run on the draft, a draft run started
   after the version was submitted stands in, marked as such (the draft may
   have moved on since). Each is compared with the same suite's latest run
   on the version this one would replace (the live one, else the approved
   one before it) by the regression gate's own rule (`evals/gate.py`).
2. **Safety: no unapproved write.** Every tool call those runs made, checked
   by `evals/safety.py`. Expected: none. And how the cases that expect an
   approval to be asked for (`expects_approval`) did.
3. **Cost forecast.** The average turn cost of those runs' conversations,
   times the conversations a month the reviewer expects, as a range of
   ±30%. An estimate, and labelled one: an eval conversation is one
   question, and real ones have follow-ups.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.evals import safety
from app.evals.gate import Baseline, regressions
from app.models.assistant import Assistant, AssistantVersion, VersionStatus
from app.models.conversation import Conversation, ToolCall
from app.models.evals import EvalCaseResult, EvalRun, EvalRunStatus, EvalSuite
from app.schemas.approval_evidence import (
    CostForecastOut,
    FlaggedCallOut,
    SafetyOut,
    SuiteScoreOut,
    VersionEvidenceOut,
)
from app.services import assistants as assistants_svc

#: Expected conversations a month when the reviewer hasn't said.
DEFAULT_PER_MONTH = 500
#: The forecast's range either side of the middle.
SPREAD = 0.3


async def _latest_run(
    session: AsyncSession, suite_id: uuid.UUID, version: AssistantVersion | None
) -> EvalRun | None:
    stmt = select(EvalRun).where(EvalRun.suite_id == suite_id, EvalRun.status == EvalRunStatus.done)
    stmt = stmt.where(
        EvalRun.assistant_version_id == version.id
        if version is not None
        else EvalRun.assistant_version_id.is_(None)
    )
    return await session.scalar(stmt.order_by(EvalRun.created_at.desc()).limit(1))


async def _draft_run_since(
    session: AsyncSession, suite_id: uuid.UUID, version: AssistantVersion
) -> EvalRun | None:
    """A draft run started once this version was submitted."""
    return await session.scalar(
        select(EvalRun)
        .where(
            EvalRun.suite_id == suite_id,
            EvalRun.status == EvalRunStatus.done,
            EvalRun.assistant_version_id.is_(None),
            EvalRun.created_at >= version.created_at,
        )
        .order_by(EvalRun.created_at.desc())
        .limit(1)
    )


async def _compared_with(
    session: AsyncSession, assistant: Assistant, version: AssistantVersion
) -> AssistantVersion | None:
    """What this version would replace: the live one, or else the latest
    approved one before it."""
    if assistant.current_version_id is not None and assistant.current_version_id != version.id:
        live = await session.get(AssistantVersion, assistant.current_version_id)
        if live is not None:
            return live
    return await session.scalar(
        select(AssistantVersion)
        .where(
            AssistantVersion.assistant_id == assistant.id,
            AssistantVersion.status == VersionStatus.approved,
            AssistantVersion.version_number < version.version_number,
        )
        .order_by(AssistantVersion.version_number.desc())
        .limit(1)
    )


def _score(
    suite: EvalSuite, run: EvalRun | None, ran_on: str | None, previous: EvalRun | None
) -> SuiteScoreOut:
    if run is None:
        return SuiteScoreOut(suite_id=suite.id, suite_name=suite.name)
    m: dict[str, Any] = dict(run.metrics or {})
    reasons: list[str] = []
    previous_rate = None
    if previous is not None:
        before = dict(previous.metrics or {})
        previous_rate = before.get("pass_rate")
        reasons = regressions(
            m, Baseline(suite=suite.name, metrics=before, cases=int(before.get("cases") or 0))
        )
    return SuiteScoreOut(
        suite_id=suite.id,
        suite_name=suite.name,
        run_id=run.id,
        ran_on="version" if ran_on == "version" else "draft",
        finished_at=run.finished_at,
        cases=int(m.get("cases") or 0),
        passed=int(m.get("passed") or 0),
        failed=int(m.get("failed") or 0),
        errors=int(m.get("errors") or 0),
        pass_rate=m.get("pass_rate"),
        previous_pass_rate=previous_rate,
        regressions=reasons,
    )


async def _safety(session: AsyncSession, run_ids: Sequence[uuid.UUID]) -> SafetyOut:
    calls: list[tuple[ToolCall, uuid.UUID]] = []
    approval_cases = approval_passed = 0
    if run_ids:
        rows = await session.execute(
            select(ToolCall, Conversation.eval_run_id)
            .join(Conversation, Conversation.id == ToolCall.conversation_id)
            .where(Conversation.eval_run_id.in_(run_ids))
            .order_by(ToolCall.created_at)
        )
        calls = [(c, rid) for c, rid in rows.tuples().all() if rid is not None]
        results = await session.scalars(
            select(EvalCaseResult).where(EvalCaseResult.eval_run_id.in_(run_ids))
        )
        for r in results:
            if not (r.expected or {}).get("expects_approval"):
                continue
            approval_cases += 1
            checks = (r.scores or {}).get("checks") or []
            if any(c.get("kind") == "approval" and c.get("passed") for c in checks):
                approval_passed += 1
    as_dicts = [
        ({"name": c.tool_name, "input": c.input, "permission": c.permission, "status": c.status})
        for c, _ in calls
    ]
    flagged = [
        FlaggedCallOut(tool=c.tool_name, run_id=rid, conversation_id=c.conversation_id)
        for (c, rid), d in zip(calls, as_dicts, strict=True)
        if safety.unapproved_write(d)
    ]
    return SafetyOut(
        checked_calls=len(calls),
        writes_without_approval=len(flagged),
        approval_requests=sum(1 for d in as_dicts if d["permission"] in safety.ASKED),
        approval_cases=approval_cases,
        approval_cases_passed=approval_passed,
        ok=not flagged,
        flagged=flagged,
    )


async def _cost(
    session: AsyncSession, run_ids: Sequence[uuid.UUID], per_month: int
) -> CostForecastOut:
    count, total = 0, 0.0
    if run_ids:
        row = (
            await session.execute(
                select(func.count(), func.coalesce(func.sum(Conversation.cost_usd), 0)).where(
                    Conversation.eval_run_id.in_(run_ids)
                )
            )
        ).one()
        count, total = int(row[0] or 0), float(row[1] or 0)
    if count == 0:
        return CostForecastOut(
            conversations_measured=0,
            per_month=per_month,
            note="No eval conversations on this version yet, so there is nothing to estimate from.",
        )
    avg = total / count
    mid = avg * per_month
    return CostForecastOut(
        conversations_measured=count,
        avg_cost_usd=round(avg, 6),
        per_month=per_month,
        mid_usd=round(mid, 2),
        low_usd=round(mid * (1 - SPREAD), 2),
        high_usd=round(mid * (1 + SPREAD), 2),
        note=(
            f"An estimate from {count} eval conversation{'s' if count != 1 else ''}, "
            "shown ±30%. Each is a single question, so conversations with follow-ups "
            "will cost more."
        ),
    )


async def evidence(
    session: AsyncSession, assistant: Assistant, number: int, *, per_month: int
) -> VersionEvidenceOut:
    version = await assistants_svc.get_version(session, assistant.id, number)
    previous = await _compared_with(session, assistant, version)
    suites = (
        await session.scalars(
            select(EvalSuite)
            .where(EvalSuite.assistant_id == assistant.id)
            .order_by(EvalSuite.created_at)
        )
    ).all()
    scorecard: list[SuiteScoreOut] = []
    run_ids: list[uuid.UUID] = []
    on_draft = missing = 0
    for suite in suites:
        run, ran_on = await _latest_run(session, suite.id, version), "version"
        if run is None:
            run, ran_on = await _draft_run_since(session, suite.id, version), "draft"
        if run is None:
            missing += 1
        else:
            run_ids.append(run.id)
            on_draft += ran_on == "draft"
        before = await _latest_run(session, suite.id, previous) if previous else None
        scorecard.append(_score(suite, run, ran_on if run else None, before))

    notes: list[str] = []
    if not suites:
        notes.append(
            "This assistant has no eval suites. Add one in the Evals tab to see a scorecard here."
        )
    elif missing:
        notes.append(
            f"{missing} of {len(suites)} suites haven't been run on version {number}. "
            "Run them from the Evals tab and choose this version."
        )
    if on_draft:
        notes.append(
            "Some results come from the draft after this version was submitted. "
            "If the draft changed since, they may not match this version."
        )
    return VersionEvidenceOut(
        version_number=number,
        compared_with=previous.version_number if previous else None,
        scorecard=scorecard,
        safety=await _safety(session, run_ids),
        cost=await _cost(session, run_ids, per_month),
        notes=notes,
    )


__all__ = ["DEFAULT_PER_MONTH", "SPREAD", "evidence"]
