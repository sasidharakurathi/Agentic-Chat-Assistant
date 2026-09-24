"""What an approval guarantees (task 3.8 remaining gaps).

- What the reviewer approved is exactly what runs: the permission callback
  returns a copy pinned before the wait (`updated_input`), so a change to
  the live input dict during the wait cannot reach execution.
- The approval row records the SDK's `tool_use_id`, linking it to the tool
  call it gated.
- `APPROVAL_TIMEOUT_S` is honoured. It existed and nothing read it.
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from app.agent.approvals import ApprovalRequest, build_can_use_tool
from app.db.session import get_sessionmaker
from app.models.approval import Approval, ApprovalStatus
from app.schemas.assistant_config import ApprovalPolicy
from httpx import AsyncClient
from sqlalchemy import select
from tests.test_chat_approvals import (
    _assistant_with_db,
    _await_prompt,
    _conversation,
    _run_turn,
    fake_sql,
)

__all__ = ["fake_sql"]  # a fixture, imported for use by name

pytestmark = pytest.mark.anyio


async def test_what_was_approved_is_what_runs() -> None:
    live: dict[str, Any] = {"sql": "UPDATE t SET a = 1 WHERE id = 7"}

    async def reviewer(_t: str, shown: dict, _r: str, _w: str, **_k: object) -> str:
        # Something rewrites the live dict while the human is deciding.
        live["sql"] = "DELETE FROM t"
        assert shown["sql"] == "UPDATE t SET a = 1 WHERE id = 7", "the reviewer saw the original"
        return "approved"

    can_use = build_can_use_tool(
        ApprovalPolicy(),
        ApprovalRequest(conversation_id=uuid.uuid4(), org_id=uuid.uuid4(), request=reviewer),
    )
    result = await can_use("mcp__caps__sql_query", live, None)  # type: ignore[arg-type]
    assert type(result).__name__ == "PermissionResultAllow"
    assert result.updated_input == {"sql": "UPDATE t SET a = 1 WHERE id = 7"}


async def test_the_tool_use_id_reaches_the_requester() -> None:
    got: dict[str, Any] = {}

    async def reviewer(*_a: object, tool_use_id: str | None = None, **_k: object) -> str:
        got["id"] = tool_use_id
        return "denied"

    can_use = build_can_use_tool(
        ApprovalPolicy(),
        ApprovalRequest(conversation_id=uuid.uuid4(), org_id=uuid.uuid4(), request=reviewer),
    )
    ctx = SimpleNamespace(tool_use_id="toolu_01ABC")
    await can_use("mcp__caps__sql_query", {"sql": "DELETE FROM t"}, ctx)  # type: ignore[arg-type]
    assert got["id"] == "toolu_01ABC"


async def test_the_timeout_setting_is_honoured(
    client: AsyncClient,
    org_headers: dict[str, str],
    fake_sql: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.services.chat.settings.approval_timeout_s", 1)
    aid = await _assistant_with_db(client, org_headers)
    cid = await _conversation(client, org_headers, aid)
    events: list[Any] = []
    loop = asyncio.get_running_loop()
    started = loop.time()
    turn = asyncio.create_task(_run_turn(cid, "sql: DELETE FROM t WHERE id = 1", events))
    prompt = await _await_prompt(events)
    await asyncio.wait_for(turn, timeout=20)

    assert loop.time() - started < 10, "the hard-coded 300 s still applied"
    assert fake_sql["ran"] == []
    async with get_sessionmaker()() as s:
        row = await s.scalar(select(Approval).where(Approval.id == uuid.UUID(prompt.approval_id)))
        assert row is not None
        assert row.status is ApprovalStatus.expired
        assert (row.expires_at - row.created_at).total_seconds() < 5
