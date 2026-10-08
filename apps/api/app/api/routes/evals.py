"""Evals (task 6.1, plan §8): suites of questions for one assistant, run
against a chosen version and scored.

Anyone in the org can read suites and results. Creating, editing and
running them is for the assistant's editors: a run takes real turns, and on
a real model real money.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Path, status

from app import queue
from app.agent import claude_api
from app.api.deps import (
    AssistantCtx,
    CurrentUser,
    EditableAssistantCtx,
    SessionDep,
    per_user_limit,
)
from app.api.errors import Forbidden, NotFound
from app.db.tenancy import bind_org
from app.models.assistant import Assistant
from app.models.enums import MemberRole
from app.models.evals import EvalCase, EvalCaseResult, EvalRun, EvalSuite
from app.models.membership import Membership
from app.schemas.common import Message
from app.schemas.evals import (
    EvalCaseIn,
    EvalCaseOut,
    EvalCaseResultOut,
    EvalCasesBulkIn,
    EvalExpected,
    EvalLabels,
    EvalRunDetail,
    EvalRunIn,
    EvalRunsOut,
    EvalRunSummary,
    EvalSuiteConfig,
    EvalSuiteDetail,
    EvalSuiteIn,
    EvalSuiteOut,
    EvalSuitePatch,
    EvalSuitesOut,
)
from app.security.access import member_of
from app.services import evals as svc

router = APIRouter(tags=["evals"])

#: Starting a run starts a job that takes turns.
HEAVY = per_user_limit("heavy", "rate_limit_heavy")


# ── who is asking, about which suite or run ──────────────────


@dataclass
class SuiteContext:
    suite: EvalSuite
    assistant: Assistant
    membership: Membership

    @property
    def can_edit(self) -> bool:
        return self.membership.role.satisfies(MemberRole.admin) or (
            self.assistant.created_by is not None
            and self.assistant.created_by == self.membership.user_id
        )


async def _context(session: SessionDep, user: CurrentUser, suite: EvalSuite | None) -> SuiteContext:
    """Not found, rather than forbidden, for anything outside the caller's
    orgs: whether a suite exists elsewhere is not theirs to learn."""
    if suite is None:
        raise NotFound("Eval suite not found")
    membership = await member_of(session, suite.org_id, user.id, missing="Eval suite not found")
    assistant = await session.get(Assistant, suite.assistant_id)
    if assistant is None:
        raise NotFound("Eval suite not found")
    bind_org(session, suite.org_id)
    return SuiteContext(suite=suite, assistant=assistant, membership=membership)


async def suite_context(
    session: SessionDep, user: CurrentUser, suite_id: Annotated[uuid.UUID, Path()]
) -> SuiteContext:
    return await _context(session, user, await session.get(EvalSuite, suite_id))


async def suite_editor(ctx: Annotated[SuiteContext, Depends(suite_context)]) -> SuiteContext:
    if not ctx.can_edit:
        raise Forbidden(
            "You can only change evals for assistants you created (or need an admin role)",
            code="not_assistant_editor",
        )
    return ctx


SuiteCtx = Annotated[SuiteContext, Depends(suite_context)]
SuiteEditorCtx = Annotated[SuiteContext, Depends(suite_editor)]


@dataclass
class RunContext:
    run: EvalRun
    suite: SuiteContext


async def run_context(
    session: SessionDep, user: CurrentUser, run_id: Annotated[uuid.UUID, Path()]
) -> RunContext:
    run = await session.get(EvalRun, run_id)
    if run is None:
        raise NotFound("Eval run not found")
    ctx = await _context(session, user, await session.get(EvalSuite, run.suite_id))
    return RunContext(run=run, suite=ctx)


RunCtx = Annotated[RunContext, Depends(run_context)]


# ── shapes ───────────────────────────────────────────────────


def _case(case: EvalCase) -> EvalCaseOut:
    return EvalCaseOut(
        id=case.id,
        position=case.position,
        input=case.input,
        expected=EvalExpected.model_validate(case.expected or {}),
        labels=EvalLabels.model_validate(case.labels or {}),
        metadata=case.meta or {},
    )


def _run(run: EvalRun) -> EvalRunSummary:
    return EvalRunSummary(
        id=run.id,
        suite_id=run.suite_id,
        status=run.status.value,
        assistant_version_id=run.assistant_version_id,
        version_number=run.version_number,
        metrics=run.metrics or {},
        cost_usd=round(float(run.cost_usd), 6),
        total_cases=run.total_cases,
        done_cases=run.done_cases,
        error=run.error,
        created_at=run.created_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
    )


def _result(row: EvalCaseResult) -> EvalCaseResultOut:
    return EvalCaseResultOut(
        id=row.id,
        eval_case_id=row.eval_case_id,
        position=row.position,
        input=row.input,
        expected=EvalExpected.model_validate(row.expected or {}),
        output=row.output,
        scores=row.scores or {},
        passed=row.passed,
        error=row.error,
        cost_usd=round(float(row.cost_usd), 6),
        duration_ms=row.duration_ms,
        conversation_id=row.conversation_id,
        run_id=row.run_id,
        trace_id=row.trace_id,
    )


def _suite(suite: EvalSuite, case_count: int, last: EvalRun | None) -> EvalSuiteOut:
    return EvalSuiteOut(
        id=suite.id,
        assistant_id=suite.assistant_id,
        name=suite.name,
        config=EvalSuiteConfig.model_validate(suite.config or {}),
        case_count=case_count,
        last_run=_run(last) if last else None,
        created_at=suite.created_at,
    )


async def _detail(session: SessionDep, suite: EvalSuite) -> EvalSuiteDetail:
    cases = await svc.list_cases(session, suite.id)
    last = (await svc.last_runs(session, [suite.id])).get(suite.id)
    return EvalSuiteDetail(
        **_suite(suite, len(cases), last).model_dump(),
        cases=[_case(c) for c in cases],
        judge_available=claude_api.real_model_allowed(),
    )


# ── suites ───────────────────────────────────────────────────


@router.get("/assistants/{assistant_id}/eval-suites", response_model=EvalSuitesOut)
async def list_suites(ctx: AssistantCtx, session: SessionDep) -> EvalSuitesOut:
    suites = await svc.list_suites(session, ctx.assistant.id)
    ids = [s.id for s in suites]
    counts = await svc.case_counts(session, ids)
    last = await svc.last_runs(session, ids)
    return EvalSuitesOut(
        suites=[_suite(s, counts.get(s.id, 0), last.get(s.id)) for s in suites],
        can_edit=ctx.can_edit,
    )


@router.post(
    "/assistants/{assistant_id}/eval-suites",
    response_model=EvalSuiteDetail,
    status_code=status.HTTP_201_CREATED,
)
async def create_suite(
    body: EvalSuiteIn, ctx: EditableAssistantCtx, session: SessionDep
) -> EvalSuiteDetail:
    suite = await svc.create_suite(
        session,
        assistant=ctx.assistant,
        name=body.name,
        config=body.config,
        actor_id=ctx.membership.user_id,
    )
    return await _detail(session, suite)


@router.get("/eval-suites/{suite_id}", response_model=EvalSuiteDetail)
async def get_suite(ctx: SuiteCtx, session: SessionDep) -> EvalSuiteDetail:
    return await _detail(session, ctx.suite)


@router.patch("/eval-suites/{suite_id}", response_model=EvalSuiteDetail)
async def patch_suite(
    body: EvalSuitePatch, ctx: SuiteEditorCtx, session: SessionDep
) -> EvalSuiteDetail:
    suite = await svc.update_suite(session, ctx.suite, name=body.name, config=body.config)
    return await _detail(session, suite)


@router.delete("/eval-suites/{suite_id}", response_model=Message)
async def delete_suite(ctx: SuiteEditorCtx, session: SessionDep) -> Message:
    """The suite, its cases and every run of it."""
    await svc.delete_suite(session, ctx.suite, actor_id=ctx.membership.user_id)
    return Message(message="Suite deleted")


# ── cases ────────────────────────────────────────────────────


@router.post("/eval-suites/{suite_id}/cases:bulk", response_model=EvalSuiteDetail)
async def add_cases(
    body: EvalCasesBulkIn, ctx: SuiteEditorCtx, session: SessionDep
) -> EvalSuiteDetail:
    """Add cases, or replace them all (an import)."""
    await svc.add_cases(session, ctx.suite, body.cases, replace=body.mode == "replace")
    return await _detail(session, ctx.suite)


@router.put("/eval-suites/{suite_id}/cases/{case_id}", response_model=EvalCaseOut)
async def put_case(
    case_id: Annotated[uuid.UUID, Path()],
    body: EvalCaseIn,
    ctx: SuiteEditorCtx,
    session: SessionDep,
) -> EvalCaseOut:
    case = await svc.get_case(session, ctx.suite.id, case_id)
    return _case(await svc.update_case(session, case, body))


@router.delete("/eval-suites/{suite_id}/cases/{case_id}", response_model=Message)
async def delete_case(
    case_id: Annotated[uuid.UUID, Path()], ctx: SuiteEditorCtx, session: SessionDep
) -> Message:
    await svc.delete_case(session, await svc.get_case(session, ctx.suite.id, case_id))
    return Message(message="Case deleted")


# ── runs ─────────────────────────────────────────────────────


@router.post(
    "/eval-suites/{suite_id}/runs",
    response_model=EvalRunSummary,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[HEAVY],
)
async def start_run(body: EvalRunIn, ctx: SuiteEditorCtx, session: SessionDep) -> EvalRunSummary:
    """Queue a run against a published version, or the draft. Poll
    `GET /eval-runs/{id}` for progress and results."""
    run = await svc.create_run(
        session,
        ctx.suite,
        version_id=body.assistant_version_id,
        actor_id=ctx.membership.user_id,
    )
    if not await queue.enqueue_eval(run.id):
        run = await svc.mark_unstarted(session, run)
    return _run(run)


@router.get("/eval-suites/{suite_id}/runs", response_model=EvalRunsOut)
async def list_runs(ctx: SuiteCtx, session: SessionDep) -> EvalRunsOut:
    """The suite's recent runs, newest first."""
    return EvalRunsOut(runs=[_run(r) for r in await svc.list_runs(session, ctx.suite.id)])


@router.get("/eval-runs/{run_id}", response_model=EvalRunDetail)
async def get_run(ctx: RunCtx, session: SessionDep) -> EvalRunDetail:
    """A run's metrics and each case's result so far."""
    rows = await svc.results(session, ctx.run.id)
    return EvalRunDetail(**_run(ctx.run).model_dump(), results=[_result(r) for r in rows])


@router.post("/eval-runs/{run_id}:cancel", response_model=EvalRunSummary)
async def cancel_run(ctx: RunCtx, session: SessionDep) -> EvalRunSummary:
    if not ctx.suite.can_edit:
        raise Forbidden(
            "You can only cancel runs for assistants you created (or need an admin role)",
            code="not_assistant_editor",
        )
    return _run(await svc.cancel_run(session, ctx.run))


__all__ = ["router"]
