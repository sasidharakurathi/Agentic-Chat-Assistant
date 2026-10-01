"""Eval suites, cases and runs (task 6.1): the part a request does. The run
itself is `evals/runner.py`, in the worker.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import BadRequest, Conflict, NotFound
from app.models.assistant import Assistant, AssistantVersion
from app.models.evals import EvalCase, EvalCaseResult, EvalRun, EvalRunStatus, EvalSuite
from app.schemas.evals import MAX_CASES, EvalCaseIn, EvalSuiteConfig
from app.services import audit

#: Runs shown for a suite, newest first.
RUNS_SHOWN = 30
NO_WORKER = "The background worker couldn't be reached, so the run didn't start. Try again."


async def list_suites(session: AsyncSession, assistant_id: uuid.UUID) -> list[EvalSuite]:
    rows = await session.scalars(
        select(EvalSuite)
        .where(EvalSuite.assistant_id == assistant_id)
        .order_by(EvalSuite.created_at)
    )
    return list(rows.all())


async def case_counts(session: AsyncSession, suite_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not suite_ids:
        return {}
    rows = await session.execute(
        select(EvalCase.suite_id, func.count())
        .where(EvalCase.suite_id.in_(suite_ids))
        .group_by(EvalCase.suite_id)
    )
    return {suite_id: int(n) for suite_id, n in rows.all()}


async def last_runs(session: AsyncSession, suite_ids: list[uuid.UUID]) -> dict[uuid.UUID, EvalRun]:
    """Each suite's newest run."""
    out: dict[uuid.UUID, EvalRun] = {}
    if not suite_ids:
        return out
    rows = await session.scalars(
        select(EvalRun).where(EvalRun.suite_id.in_(suite_ids)).order_by(EvalRun.created_at.desc())
    )
    for run in rows.all():
        out.setdefault(run.suite_id, run)
    return out


async def create_suite(
    session: AsyncSession,
    *,
    assistant: Assistant,
    name: str,
    config: EvalSuiteConfig,
    actor_id: uuid.UUID,
) -> EvalSuite:
    suite = EvalSuite(
        assistant_id=assistant.id,
        org_id=assistant.org_id,
        name=name.strip(),
        config=config.model_dump(),
        created_by=actor_id,
    )
    session.add(suite)
    await session.flush()
    await audit.record(
        session,
        org_id=assistant.org_id,
        actor_user_id=actor_id,
        action="eval_suite.create",
        target_type="eval_suite",
        target_id=str(suite.id),
        meta={"name": suite.name},
    )
    await session.commit()
    return suite


async def update_suite(
    session: AsyncSession, suite: EvalSuite, *, name: str | None, config: EvalSuiteConfig | None
) -> EvalSuite:
    if name is not None:
        suite.name = name.strip()
    if config is not None:
        suite.config = config.model_dump()
    await session.commit()
    return suite


async def delete_suite(session: AsyncSession, suite: EvalSuite, *, actor_id: uuid.UUID) -> None:
    await audit.record(
        session,
        org_id=suite.org_id,
        actor_user_id=actor_id,
        action="eval_suite.delete",
        target_type="eval_suite",
        target_id=str(suite.id),
        meta={"name": suite.name},
    )
    await session.delete(suite)
    await session.commit()


async def list_cases(session: AsyncSession, suite_id: uuid.UUID) -> list[EvalCase]:
    rows = await session.scalars(
        select(EvalCase).where(EvalCase.suite_id == suite_id).order_by(EvalCase.position)
    )
    return list(rows.all())


def _fields(case: EvalCaseIn) -> dict[str, Any]:
    return {
        "input": case.input,
        "expected": case.expected.model_dump(),
        "labels": case.labels.model_dump(),
        "meta": case.metadata,
    }


async def add_cases(
    session: AsyncSession, suite: EvalSuite, cases: list[EvalCaseIn], *, replace: bool
) -> list[EvalCase]:
    """Append to the suite, or swap every case for these. A suite's size is
    capped: every case is a real turn when it runs."""
    existing = [] if replace else await list_cases(session, suite.id)
    if len(existing) + len(cases) > MAX_CASES:
        raise BadRequest(
            f"A suite can hold {MAX_CASES} cases. This would make {len(existing) + len(cases)}. "
            "Remove some, or split them into another suite.",
            code="too_many_cases",
        )
    if replace:
        await session.execute(delete(EvalCase).where(EvalCase.suite_id == suite.id))
    start = max((c.position for c in existing), default=-1) + 1
    for offset, case in enumerate(cases):
        session.add(
            EvalCase(
                suite_id=suite.id, org_id=suite.org_id, position=start + offset, **_fields(case)
            )
        )
    await session.commit()
    return await list_cases(session, suite.id)


async def get_case(session: AsyncSession, suite_id: uuid.UUID, case_id: uuid.UUID) -> EvalCase:
    case = await session.get(EvalCase, case_id)
    if case is None or case.suite_id != suite_id:
        raise NotFound("Case not found")
    return case


async def update_case(session: AsyncSession, case: EvalCase, body: EvalCaseIn) -> EvalCase:
    for key, value in _fields(body).items():
        setattr(case, key, value)
    await session.commit()
    return case


async def delete_case(session: AsyncSession, case: EvalCase) -> None:
    await session.delete(case)
    await session.commit()


async def _version(
    session: AsyncSession, assistant_id: uuid.UUID, version_id: uuid.UUID | None
) -> AssistantVersion | None:
    if version_id is None:
        return None
    version = await session.get(AssistantVersion, version_id)
    if version is None or version.assistant_id != assistant_id:
        raise NotFound("That version doesn't belong to this assistant")
    return version


async def create_run(
    session: AsyncSession,
    suite: EvalSuite,
    *,
    version_id: uuid.UUID | None,
    actor_id: uuid.UUID,
) -> EvalRun:
    """A queued run. One at a time per suite: two runs of the same cases
    would only compete for the same turn slots."""
    total = (await case_counts(session, [suite.id])).get(suite.id, 0)
    if total == 0:
        raise BadRequest("Add at least one case before running the suite.", code="no_cases")
    active = await session.scalar(
        select(EvalRun.id).where(
            EvalRun.suite_id == suite.id,
            EvalRun.status.in_([EvalRunStatus.queued, EvalRunStatus.running]),
        )
    )
    if active is not None:
        raise Conflict(
            "This suite is already running. Wait for it to finish, or cancel it.",
            code="eval_run_active",
        )
    version = await _version(session, suite.assistant_id, version_id)
    run = EvalRun(
        suite_id=suite.id,
        assistant_id=suite.assistant_id,
        org_id=suite.org_id,
        assistant_version_id=version.id if version else None,
        version_number=version.version_number if version else None,
        status=EvalRunStatus.queued,
        total_cases=total,
        created_by=actor_id,
    )
    session.add(run)
    await session.flush()
    await audit.record(
        session,
        org_id=suite.org_id,
        actor_user_id=actor_id,
        action="eval_run.start",
        target_type="eval_run",
        target_id=str(run.id),
        meta={"suite": suite.name, "version": run.version_number, "cases": total},
    )
    await session.commit()
    return run


async def mark_unstarted(session: AsyncSession, run: EvalRun) -> EvalRun:
    """The queue was down: say so on the run rather than leave it queued
    with nothing coming to pick it up."""
    run.status = EvalRunStatus.failed
    run.error = NO_WORKER
    await session.commit()
    return run


async def list_runs(session: AsyncSession, suite_id: uuid.UUID) -> list[EvalRun]:
    rows = await session.scalars(
        select(EvalRun)
        .where(EvalRun.suite_id == suite_id)
        .order_by(EvalRun.created_at.desc())
        .limit(RUNS_SHOWN)
    )
    return list(rows.all())


async def results(session: AsyncSession, run_id: uuid.UUID) -> list[EvalCaseResult]:
    rows = await session.scalars(
        select(EvalCaseResult)
        .where(EvalCaseResult.eval_run_id == run_id)
        .order_by(EvalCaseResult.position)
    )
    return list(rows.all())


async def cancel_run(session: AsyncSession, run: EvalRun) -> EvalRun:
    """Stop a run that is queued or running. The runner sees it before its
    next case; the case in flight finishes and is kept. Conditional in SQL,
    so a run that finished meanwhile stays finished."""
    await session.execute(
        update(EvalRun)
        .where(
            EvalRun.id == run.id,
            EvalRun.status.in_([EvalRunStatus.queued, EvalRunStatus.running]),
        )
        .values(status=EvalRunStatus.cancelled, finished_at=func.now())
    )
    await session.commit()
    await session.refresh(run)
    return run


__all__ = [
    "NO_WORKER",
    "add_cases",
    "cancel_run",
    "case_counts",
    "create_run",
    "create_suite",
    "delete_case",
    "delete_suite",
    "get_case",
    "last_runs",
    "list_cases",
    "list_runs",
    "list_suites",
    "mark_unstarted",
    "results",
    "update_case",
    "update_suite",
]
