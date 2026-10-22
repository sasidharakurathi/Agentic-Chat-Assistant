"""The one place an approved request runs (Phase 9.1, plan D17).

Nothing about a request is trusted from the moment it was asked: by the
time someone decides, hours may have passed. So the executor:

1. **Claims it**: `approved → executing` in one conditional UPDATE. A
   second delivery of the same job (the queue delivers at least once), a
   retried hand-over or a sweep finds nothing to claim and stops. It runs
   once, or not at all.
2. **Re-checks everything**, as the person who asked, never as the
   approver: the assistant still exists and isn't paused; a live request's
   version is still in service; the asker still has an active account and
   membership, and is still in the assistant's audience (or, in a preview,
   can still edit it); the tool or action is still part of that version.
3. **Runs the sealed input as it is**, checked against its hash, through
   the same tool handler a turn would use, so every guard applies again:
   the SQL guard, the connection's permissions and `expose_write`, the HTTP
   allowlist, the MCP server's own rules. The model is never asked again.
4. **Records what happened**: `succeeded` or `failed` with the output, the
   sealed input wiped, an audit entry with the hash, a line in the
   conversation (the next turn is told), and an email to the asker when an
   approver decided.

A request still `executing` after `approvals.STUCK_AFTER` (the process
died) is marked failed by the sweep and never retried by itself: a write
may have half happened, and only a person can check.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import actions
from app.agent.actions import Asker
from app.agent.caps import CAPS_SERVER_NAME
from app.agent.post_tool import run_capability
from app.config import settings
from app.db.session import get_sessionmaker
from app.logging import get_logger
from app.models.approval import Approval, ApprovalStatus
from app.models.assistant import Assistant, AssistantStatus, AssistantVersion, VersionStatus
from app.models.user import User
from app.observability import metrics
from app.schemas.assistant_config import AssistantConfig
from app.security.redact import strip_secrets
from app.services import approvals, audit

log = get_logger(__name__)

#: Versions whose requests may still run: the live one, and one a newer
#: approval replaced (its conversations stay on it, Phase 7b.1).
IN_SERVICE = frozenset({VersionStatus.approved, VersionStatus.superseded})

_running: set[asyncio.Task[Any]] = set()


def _qual(name: str) -> str:
    return f"mcp__{CAPS_SERVER_NAME}__{name}"


async def dispatch(approval_id: uuid.UUID) -> None:
    """Hand an approved request over to run. Queued in production (the job
    id makes a second hand-over a no-op), in this process otherwise."""
    if settings.executor_mode == "queue":
        from app.queue import get_queue
        from app.services.mail import MAIL_QUEUE

        try:
            queue = await get_queue()
            await queue.enqueue_job(
                "execute_request_job",
                str(approval_id),
                _job_id=f"request:{approval_id}",
                _queue_name=MAIL_QUEUE,
            )
            return
        except Exception as exc:
            # The sweep hands it over again in a couple of minutes.
            log.warning(
                "request_enqueue_failed", approval_id=str(approval_id), error=str(exc)[:200]
            )
            return
    task = asyncio.create_task(execute(approval_id))
    _running.add(task)
    task.add_done_callback(_running.discard)


async def drain() -> None:
    """Wait for requests running in this process (tests, shutdown)."""
    while _running:
        await asyncio.gather(*list(_running), return_exceptions=True)


async def config_of(session: AsyncSession, row: Approval) -> AssistantConfig | None:
    """The config the request was made under: its version's, or for a
    preview of the draft, the draft as it is now."""
    if row.assistant_version_id is not None:
        version = await session.get(AssistantVersion, row.assistant_version_id)
        return AssistantConfig.model_validate(version.config) if version else None
    if row.assistant_id is None:
        return None
    assistant = await session.get(Assistant, row.assistant_id)
    return AssistantConfig.model_validate(assistant.draft_config) if assistant else None


async def asker_of(session: AsyncSession, row: Approval) -> Asker | None:
    if row.requested_by is None:
        return None
    user = await session.get(User, row.requested_by)
    if user is None:
        return None
    return Asker(user_id=user.id, email=user.email, name=user.name or user.email)


async def why_not(session: AsyncSession, row: Approval) -> str | None:  # noqa: PLR0911 - one per check
    """Why the request may no longer run, in words the asker understands;
    None when it may."""
    from app.api.errors import AppError
    from app.security.access import can_edit_assistant, member_of
    from app.services import listing

    assistant = await session.get(Assistant, row.assistant_id) if row.assistant_id else None
    if assistant is None:
        return "The assistant was removed."
    if assistant.status is AssistantStatus.archived:
        return "The assistant has been paused by an admin."
    if row.requested_by is None:
        return "The person who asked no longer has an account."
    user = await session.get(User, row.requested_by)
    if user is None or not user.is_active:
        return "The person who asked no longer has an active account."
    try:
        member = await member_of(session, row.org_id, user.id, missing="gone", chat=True)
    except AppError:
        return "The person who asked is no longer a member here."
    if row.preview:
        if not await can_edit_assistant(session, assistant, member):
            return "The person who asked can no longer edit this assistant."
        return None
    if assistant.status is not AssistantStatus.published:
        return "The assistant isn't answering in Chat any more."
    version = (
        await session.get(AssistantVersion, row.assistant_version_id)
        if row.assistant_version_id
        else None
    )
    if version is None or version.status not in IN_SERVICE:
        return "The version of the assistant it was asked in was taken out of service."
    if not await listing.can_see(session, assistant, member):
        return "The person who asked can no longer use this assistant."
    return None


async def execute(approval_id: uuid.UUID) -> ApprovalStatus | None:
    """Claim, re-check, run once, record. Returns how it ended, or None
    when there was nothing to claim (already run, or never approved)."""
    async with get_sessionmaker()() as session:
        now = datetime.now(UTC)
        claimed = await session.scalar(
            update(Approval)
            .where(Approval.id == approval_id, Approval.status == ApprovalStatus.approved)
            .values(status=ApprovalStatus.executing, executed_at=now)
            .returning(Approval.id)
            .execution_options(synchronize_session=False)
        )
        await session.commit()
        if claimed is None:
            return None
        row = await session.get(Approval, approval_id)
        assert row is not None
        await session.refresh(row)
        try:
            ok, text = await _run(session, row)
        except Exception as exc:
            log.exception("request_run_crashed", approval_id=str(approval_id))
            ok, text = False, f"It couldn't run: {str(exc)[:300]}"
        status = ApprovalStatus.succeeded if ok else ApprovalStatus.failed
        result = strip_secrets(text)[: approvals.RESULT_CHARS]
        await session.execute(
            update(Approval)
            .where(Approval.id == approval_id, Approval.status == ApprovalStatus.executing)
            .values(status=status, result=result, **approvals.WIPED)
            .execution_options(synchronize_session=False)
        )
        await session.refresh(row)
        await audit.record(
            session,
            action="approval.executed" if ok else "approval.failed",
            org_id=row.org_id,
            actor_user_id=row.requested_by,
            target_type="approval",
            target_id=row.id,
            meta={**approvals._facts(row), "result": result[:500]},
        )
        await session.commit()
        metrics.REQUESTS_RUN.inc(status.value)
        await approvals.finished(session, row, status)
        return status


async def _run(session: AsyncSession, row: Approval) -> tuple[bool, str]:  # noqa: PLR0911, PLR0912
    """Run the sealed input with the version's own tools. (ok, output)."""
    try:
        payload = approvals.unseal(row)
    except ValueError as exc:
        return False, str(exc)
    if (why := await why_not(session, row)) is not None:
        return False, why
    config = await config_of(session, row)
    if config is None or row.assistant_id is None:
        return False, "The assistant's settings are gone."
    tool = str(payload.get("tool", ""))
    tool_input = payload.get("input") or {}

    if row.action_name is not None:
        found = actions.find(config, tool)
        if found is None:
            return await _run_http(session, row, config, tool, payload)
        if found[1].tier == "read":
            return False, "This action is no longer part of the assistant."
        ref, _action = found
        statement = str(payload.get("statement") or "")
        asker = await asker_of(session, row)
        return _outcome(
            await actions.run_statement(
                row.assistant_id, ref.connection_id, statement, asker.email if asker else None
            )
        )

    if tool == _qual("sql_query"):
        from app.agent.approvals import classify
        from app.agent.caps_sql import build_sql_tools

        mode, _ = classify(tool, tool_input, config.approval_policy, config.databases)
        if mode == "deny":
            return False, "The assistant's settings no longer allow this statement."
        handlers = {
            t.name: t.handler
            for t in build_sql_tools(
                row.assistant_id, config.databases, await asker_of(session, row)
            )
        }
        if "sql_query" not in handlers:
            return False, "The assistant no longer has this database."
        return _outcome(await run_capability(handlers["sql_query"], tool_input, tool))

    if tool == _qual("http_request"):
        from app.agent.caps_http import build_http_tool

        if not config.tools.http_request.enabled or config.tools.http_request.approval == "deny":
            return False, "The assistant no longer sends requests to other systems."
        handler = build_http_tool(config.tools.http_request, org_id=row.org_id).handler
        return _outcome(await run_capability(handler, tool_input, tool))

    if tool.startswith("mcp__") and not tool.startswith(f"mcp__{CAPS_SERVER_NAME}__"):
        from app.agent.caps_mcp import build_mcp_toolset

        toolset = await build_mcp_toolset(config, row.assistant_id)
        try:
            match = next((t for t in toolset.tools if t.qualified_name == tool), None)
            if match is None or toolset.modes.get(tool) == "deny":
                return False, "The assistant no longer offers this tool."
            return _outcome(await run_capability(match.handler, tool_input, tool))
        finally:
            await toolset.aclose()

    return False, "This kind of request can't be run."


async def _run_http(
    session: AsyncSession,
    row: Approval,
    config: AssistantConfig,
    tool: str,
    payload: dict[str, Any],
) -> tuple[bool, str]:
    """Send an HTTP action's sealed call (Phase 9.3), to the address it was
    asked for: a connection moved since then is refused, not followed."""
    from app.agent import http_actions

    action = http_actions.find_http(config, tool)
    if action is None or action.tier == "read" or row.assistant_id is None:
        return False, "This action is no longer part of the assistant."
    raw = payload.get("request")
    try:
        call = http_actions.HttpCall.of(raw)
    except ValueError as exc:
        return False, str(exc)
    conn = await http_actions.connection(row.assistant_id, action.connection_id)
    if conn is None:
        return False, "The system this action calls was removed from the assistant."
    if call.method != action.method or (raw or {}).get("base_url") != conn.base_url:
        return False, "The system's address changed after this was asked, so it wasn't sent."
    if action.tier == "confirm" and not conn.send_asker:
        return False, "The system is no longer told who is asking, so this wasn't sent."
    asker = await asker_of(session, row)
    return _outcome(await http_actions.send(row.assistant_id, action, call, asker))


def _outcome(result: dict[str, Any]) -> tuple[bool, str]:
    text = "\n".join(
        str(part.get("text", ""))
        for part in result.get("content") or []
        if isinstance(part, dict) and part.get("type") == "text"
    )
    return not result.get("is_error"), text.strip()


__all__ = ["IN_SERVICE", "asker_of", "config_of", "dispatch", "drain", "execute", "why_not"]
