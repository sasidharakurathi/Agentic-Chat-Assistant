"""Requests in tests (Phase 9.1): store one as the router would, and decide
one as the app does (naming the hash of what was shown)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from app.db.session import get_sessionmaker
from app.models.approval import Approval, ApprovalTier
from app.models.conversation import Conversation
from app.services import approvals as approvals_svc
from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

API = "/api/v1"


async def waiting_request(
    session: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    tool_name: str = "mcp__caps__sql_query",
    tool_input: dict[str, Any] | None = None,
    risk: str = "high",
    rationale: str = "writes",
    tier: ApprovalTier = ApprovalTier.approver,
    expires_in: float | None = None,
) -> Approval:
    """A request waiting in a conversation, stored the way the router
    stores one. `expires_in` (seconds, may be negative) moves its expiry."""
    conversation = await session.get(Conversation, conversation_id)
    assert conversation is not None
    row = await approvals_svc.submit(
        session,
        conversation=conversation,
        tool_name=tool_name,
        tool_input=tool_input if tool_input is not None else {"sql": "UPDATE t SET x = 1"},
        risk=risk,
        rationale=rationale,
        tier=tier,
        title="A database change",
    )
    if expires_in is not None:
        row.expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)
        await session.commit()
    return row


async def shown_hash(approval_id: str | uuid.UUID) -> str | None:
    async with get_sessionmaker()() as s:
        return await s.scalar(
            select(Approval.input_sha256).where(Approval.id == uuid.UUID(str(approval_id)))
        )


async def decide(
    client: AsyncClient,
    headers: dict[str, str],
    approval_id: str | uuid.UUID,
    decision: str,
    *,
    note: str | None = None,
    params: dict[str, Any] | None = None,
) -> Response:
    """Decide through the API, as the card does. A decline gets a note,
    which an approver's needs."""
    body: dict[str, Any] = {"decision": decision, "input_sha256": await shown_hash(approval_id)}
    if note is not None or decision == "denied":
        body["note"] = note if note is not None else "Not this one."
    if params is not None:
        body["params"] = params
    return await client.post(f"{API}/approvals/{approval_id}:resolve", json=body, headers=headers)


__all__ = ["decide", "shown_hash", "waiting_request"]
