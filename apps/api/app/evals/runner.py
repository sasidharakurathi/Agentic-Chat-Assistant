"""Run a suite: every case through the real chat path, then score it
(task 6.1, plan §11.2).

Each case is one message in its own hidden conversation, answered by
`chat.run_message` exactly as a user's would be, except that it is pinned
to the version under test and nobody is there to approve anything
(`chat.Unattended`). So a result has what any turn has: a run row, its
steps, its trace, and its spend on the ledger and against the budgets.

Then three kinds of score, each optional:

- the free checks the case asked for (`evals/checks.py`);
- retrieval metrics, when the case has labels (`evals/labels.py`);
- the judge's rubric, when the suite wants it and the real model is on
  (`evals/judge.py`).

Cases run one after another. A run is a background job, not something
anyone waits on, and one at a time keeps an eval from taking every turn
slot from people who are chatting.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import anthropic
import anyio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import claude_api
from app.agent.events import DoneEvent, ErrorEvent, ToolCallEvent
from app.assist.structured import AssistFailed, Spend
from app.db.session import get_sessionmaker
from app.evals import judge as judge_mod
from app.evals.aggregate import summarize, verdict
from app.evals.checks import run_checks
from app.evals.labels import score_retrieval
from app.logging import get_logger
from app.models.assistant import Assistant, AssistantVersion
from app.models.conversation import Conversation, Message
from app.models.evals import EvalCase, EvalCaseResult, EvalRun, EvalRunStatus, EvalSuite
from app.models.usage import UsageEvent, UsageKind
from app.rag import usage as rag_usage
from app.schemas.assistant_config import AssistantConfig
from app.schemas.evals import EvalExpected, EvalLabels, EvalSuiteConfig
from app.services import budgets, chat

log = get_logger(__name__)

#: Error codes after which no later case could run either.
_STOPS_THE_RUN = frozenset({"budget_exceeded"})
TITLE_CHARS = 80


@dataclass
class _Turn:
    """What one case's turn produced."""

    answer: str = ""
    tools: list[str] = field(default_factory=list)
    passages: list[judge_mod.Passage] = field(default_factory=list)
    error: str | None = None
    error_code: str | None = None
    message_id: str | None = None
    run_id: str | None = None
    trace_id: str | None = None
    cost_usd: float = 0.0
    duration_ms: int = 0


async def _config(session: AsyncSession, run: EvalRun, assistant: Assistant) -> AssistantConfig:
    """The config under test: for the judge's model and retrieval settings."""
    if run.assistant_version_id is not None:
        version = await session.get(AssistantVersion, run.assistant_version_id)
        if version is not None:
            return AssistantConfig.model_validate(version.config)
    return AssistantConfig.model_validate(assistant.draft_config)


def _spend_row(run: EvalRun, spend: Spend) -> UsageEvent:
    return UsageEvent(
        org_id=run.org_id,
        assistant_id=run.assistant_id,
        kind=UsageKind.llm,
        model=spend.model,
        tokens_in=spend.tokens_in,
        tokens_out=spend.tokens_out,
        cost_usd=spend.cost_usd,
    )


async def _recover(session: AsyncSession, run: EvalRun, case: EvalCase) -> None:
    """After a failure mid-case: drop the broken transaction and reload the
    rows the rest of the case still reads (a rollback expires them)."""
    await session.rollback()
    await session.refresh(run)
    await session.refresh(case)


async def _take_turn(
    session: AsyncSession, run: EvalRun, case: EvalCase
) -> tuple[_Turn, uuid.UUID]:
    """Ask the case's question in a conversation of its own."""
    title = " ".join(case.input.split())[:TITLE_CHARS]
    conv = Conversation(
        assistant_id=run.assistant_id,
        assistant_version_id=run.assistant_version_id,
        org_id=run.org_id,
        created_by=run.created_by,
        # Named up front, so the turn doesn't pay to title it.
        title=f"Eval: {title}",
        eval_run_id=run.id,
    )
    session.add(conv)
    await session.commit()
    conv_id = conv.id

    turn = _Turn()
    started = time.perf_counter()
    pin = chat.Unattended(version_id=run.assistant_version_id)
    try:
        stream = chat.run_message(session, conversation_id=conv_id, text=case.input, unattended=pin)
        async with contextlib.aclosing(stream) as events:
            async for ev in events:
                if isinstance(ev, ToolCallEvent):
                    turn.tools.append(ev.name)
                elif isinstance(ev, ErrorEvent):
                    turn.error, turn.error_code = ev.message, ev.code
                elif isinstance(ev, DoneEvent):
                    turn.message_id, turn.run_id, turn.trace_id = (
                        ev.message_id,
                        ev.run_id,
                        ev.trace_id,
                    )
    except Exception as exc:
        # One broken case is a failed case, not a failed suite.
        log.exception("eval_turn_failed", eval_run_id=str(run.id), case_id=str(case.id))
        await _recover(session, run, case)
        turn.error = f"The turn failed before it could answer ({type(exc).__name__})."
    turn.duration_ms = int((time.perf_counter() - started) * 1000)

    if turn.message_id is not None:
        message = await session.get(Message, uuid.UUID(turn.message_id))
        if message is not None:
            turn.answer = message.content
            turn.passages = [
                judge_mod.Passage(
                    marker=int(b.get("marker", 0)),
                    title=str(b.get("title", "")),
                    text=str(b.get("snippet", "")),
                )
                for b in message.blocks or []
                if b.get("type") == "citation"
            ]
    spent = await session.scalar(select(Conversation.cost_usd).where(Conversation.id == conv_id))
    turn.cost_usd = float(spent or 0)
    return turn, conv_id


async def _retrieval(
    session: AsyncSession, run: EvalRun, case: EvalCase, labels: EvalLabels, config: AssistantConfig
) -> tuple[dict[str, Any] | None, float]:
    """Retrieval scores for a labelled case, and what the lookup cost."""
    if not labels.any:
        return None, 0.0
    if not config.rag.enabled:
        return {"error": "This version has no knowledge base, so retrieval wasn't scored."}, 0.0
    try:
        with rag_usage.meter() as used:
            scores = await score_retrieval(
                session,
                assistant_id=run.assistant_id,
                question=case.input,
                labels=labels,
                config=config.rag.retrieval,
            )
    except Exception as exc:
        log.exception("eval_retrieval_failed", eval_run_id=str(run.id), case_id=str(case.id))
        await _recover(session, run, case)
        return {"error": f"Retrieval couldn't be scored ({type(exc).__name__})."}, 0.0
    for line in used.rows():
        session.add(
            UsageEvent(
                org_id=run.org_id,
                assistant_id=run.assistant_id,
                kind=UsageKind(line.kind),
                model=line.model,
                tokens_in=line.tokens,
                tokens_out=0,
                cost_usd=line.cost_usd,
            )
        )
    return scores, used.cost_usd


async def _judge(
    session: AsyncSession,
    run: EvalRun,
    case: EvalCase,
    expected: EvalExpected,
    turn: _Turn,
    config: AssistantConfig,
    suite: EvalSuiteConfig,
) -> tuple[dict[str, Any] | None, float]:
    """The judge's verdict, and what it cost. None when it doesn't apply:
    the suite doesn't want it, the real model is off, or there is no answer
    to grade."""
    if not suite.judge or turn.error or not claude_api.real_model_allowed():
        return None, 0.0
    over = (await budgets.gate(session, run.org_id, run.assistant_id)).exceeded
    if over is not None:
        return {"error": over.exceeded_message()}, 0.0
    try:
        result, spend = await judge_mod.judge(
            config,
            question=case.input,
            answer=turn.answer,
            expected=expected,
            passages=turn.passages,
        )
    except AssistFailed as exc:
        session.add(_spend_row(run, exc.spend))  # billed all the same
        why = (
            "The judge declined to grade this answer."
            if exc.reason == "refused"
            else "The judge's verdict was cut off or malformed."
        )
        return {"error": why}, exc.spend.cost_usd
    except anthropic.APIError as exc:
        log.warning("eval_judge_failed", error=type(exc).__name__)
        return {"error": "The judge model couldn't be reached."}, 0.0
    session.add(_spend_row(run, spend))
    return result.as_dict(suite.pass_score), spend.cost_usd


async def run_case(
    session: AsyncSession,
    run: EvalRun,
    case: EvalCase,
    config: AssistantConfig,
    suite: EvalSuiteConfig,
) -> tuple[EvalCaseResult, str | None]:
    """One case, scored. The second value is the turn's error code, if any."""
    expected = EvalExpected.model_validate(case.expected or {})
    labels = EvalLabels.model_validate(case.labels or {})
    turn, conv_id = await _take_turn(session, run, case)

    checks = run_checks(
        expected, answer=turn.answer, tools=turn.tools, citations=len(turn.passages)
    )
    retrieval, retrieval_cost = await _retrieval(session, run, case, labels, config)
    judged, judge_cost = await _judge(session, run, case, expected, turn, config, suite)
    scores: dict[str, Any] = {
        "checks": [c.as_dict() for c in checks],
        "judge": judged,
        "retrieval": retrieval,
        "tools": turn.tools,
        "citations": len(turn.passages),
    }
    result = EvalCaseResult(
        eval_run_id=run.id,
        eval_case_id=case.id,
        org_id=run.org_id,
        position=case.position,
        input=case.input,
        expected=expected.model_dump(),
        output=turn.answer,
        scores=scores,
        passed=verdict(scores, turn.error),
        error=turn.error,
        cost_usd=turn.cost_usd + retrieval_cost + judge_cost,
        duration_ms=turn.duration_ms,
        conversation_id=conv_id,
        run_id=uuid.UUID(turn.run_id) if turn.run_id else None,
        trace_id=turn.trace_id,
    )
    return result, turn.error_code


async def _finish(session: AsyncSession, run_id: uuid.UUID, error: str | None) -> None:
    """Write the run's metrics and final status from its result rows."""
    run = await session.get(EvalRun, run_id, populate_existing=True)
    if run is None:
        return
    rows = (
        await session.scalars(
            select(EvalCaseResult)
            .where(EvalCaseResult.eval_run_id == run_id)
            .order_by(EvalCaseResult.position)
        )
    ).all()
    run.metrics = summarize(
        {"scores": r.scores, "passed": r.passed, "error": r.error} for r in rows
    )
    run.done_cases = len(rows)
    run.cost_usd = sum(float(r.cost_usd) for r in rows)
    run.finished_at = datetime.now(UTC)
    if run.status is not EvalRunStatus.cancelled:
        run.status = EvalRunStatus.failed if error else EvalRunStatus.done
        run.error = error
    await session.commit()


async def _cancelled(session: AsyncSession, run_id: uuid.UUID) -> bool:
    status = await session.scalar(select(EvalRun.status).where(EvalRun.id == run_id))
    return status is None or status is EvalRunStatus.cancelled


async def _run_cases(session: AsyncSession, run: EvalRun) -> str | None:
    """Every case in order. Returns why the run stopped early, if it did."""
    suite = await session.get(EvalSuite, run.suite_id)
    assistant = await session.get(Assistant, run.assistant_id)
    if suite is None or assistant is None:
        return "The suite or its assistant was deleted."
    suite_config = EvalSuiteConfig.model_validate(suite.config or {})
    config = await _config(session, run, assistant)
    cases = (
        await session.scalars(
            select(EvalCase).where(EvalCase.suite_id == suite.id).order_by(EvalCase.position)
        )
    ).all()
    run_id = run.id
    for case in cases:
        if await _cancelled(session, run_id):
            return None
        result, code = await run_case(session, run, case, config, suite_config)
        session.add(result)
        run.done_cases += 1
        run.cost_usd = float(run.cost_usd) + float(result.cost_usd)
        await session.commit()
        if code in _STOPS_THE_RUN:
            return result.error
    return None


async def run(run_id: uuid.UUID) -> None:
    """The job: take a queued run to done, failed or cancelled."""
    async with get_sessionmaker()() as session:
        run = await session.get(EvalRun, run_id)
        if run is None or run.status is not EvalRunStatus.queued:
            return  # deleted, cancelled, or already picked up
        run.status = EvalRunStatus.running
        run.started_at = datetime.now(UTC)
        await session.commit()

        error: str | None = "The run was interrupted before it finished."
        try:
            error = await _run_cases(session, run)
        except Exception as exc:
            log.exception("eval_run_failed", eval_run_id=str(run_id))
            await session.rollback()
            error = f"The run stopped unexpectedly ({type(exc).__name__})."
        finally:
            # Also reached when the job is cancelled (its timeout, a worker
            # shutdown): a run left "running" would block the suite forever.
            with anyio.CancelScope(shield=True), contextlib.suppress(asyncio.CancelledError):
                await _finish(session, run_id, error)


__all__ = ["run", "run_case"]
