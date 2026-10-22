"""What a request guarantees (task 3.8; Phase 9.1).

- What the person approved is exactly what runs: the router hands over a
  copy pinned at once, so a later change to the live input dict can't reach
  what is sealed, and the executor runs the sealed copy, checked against
  its hash.
- The request records the SDK's `tool_use_id`, linking it to the tool call.
- How long it waits: a day for the asker's own confirmation, the org's
  `action_expiry_hours` for an approver's.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from app.agent.approvals import ApprovalRequest, RequestOutcome, build_can_use_tool
from app.db.session import get_sessionmaker
from app.models.approval import Approval, ApprovalTier
from app.models.conversation import Conversation
from app.schemas.assistant_config import ApprovalPolicy
from app.services import approvals as approvals_svc
from httpx import AsyncClient
from tests.pending import waiting_request

pytestmark = pytest.mark.anyio


async def test_what_is_stored_is_a_copy_taken_at_once() -> None:
    live: dict[str, Any] = {"sql": "UPDATE t SET a = 1 WHERE id = 7"}
    stored: list[dict[str, Any]] = []

    async def store(_t: str, pinned: dict, _r: str, _w: str, **_k: object) -> RequestOutcome:
        stored.append(pinned)
        return RequestOutcome("queued", "saved")

    can_use = build_can_use_tool(
        ApprovalPolicy(),
        ApprovalRequest(conversation_id=uuid.uuid4(), org_id=uuid.uuid4(), request=store),
    )
    await can_use("mcp__caps__sql_query", live, None)  # type: ignore[arg-type]
    # Something rewrites the live dict after the call was stored.
    live["sql"] = "DELETE FROM t"
    assert stored == [{"sql": "UPDATE t SET a = 1 WHERE id = 7"}]


async def test_the_tool_use_id_reaches_the_requester() -> None:
    got: dict[str, Any] = {}

    async def store(*_a: object, tool_use_id: str | None = None, **_k: object) -> RequestOutcome:
        got["id"] = tool_use_id
        return RequestOutcome("queued", "saved")

    can_use = build_can_use_tool(
        ApprovalPolicy(),
        ApprovalRequest(conversation_id=uuid.uuid4(), org_id=uuid.uuid4(), request=store),
    )
    ctx = SimpleNamespace(tool_use_id="toolu_01ABC")
    await can_use("mcp__caps__sql_query", {"sql": "DELETE FROM t"}, ctx)  # type: ignore[arg-type]
    assert got["id"] == "toolu_01ABC"


async def _conversation_id(client: AsyncClient, headers: dict[str, str]) -> uuid.UUID:
    a = (await client.post("/api/v1/assistants", json={"name": "A"}, headers=headers)).json()
    c = await client.post(
        f"/api/v1/assistants/{a['id']}/conversations", json={"mode": "preview"}, headers=headers
    )
    return uuid.UUID(c.json()["id"])


def _hours(row: Approval) -> float:
    assert row.expires_at is not None
    return (row.expires_at - datetime.now(UTC)).total_seconds() / 3600


async def test_how_long_a_request_waits(client: AsyncClient, org_headers: dict[str, str]) -> None:
    cid = await _conversation_id(client, org_headers)
    async with get_sessionmaker()() as s:
        mine = await waiting_request(s, conversation_id=cid, tier=ApprovalTier.asker)
        theirs = await waiting_request(s, conversation_id=cid)
    assert 23.9 < _hours(mine) <= 24, "a day to confirm one's own"
    assert 71.9 < _hours(theirs) <= 72, "the org's default for an approver"

    r = await client.patch(
        f"/api/v1/orgs/{org_headers['X-Org-Id']}/settings",
        json={"action_expiry_hours": 5, "action_reminder_hours": 2},
        headers=org_headers,
    )
    assert r.status_code == 200, r.text
    async with get_sessionmaker()() as s:
        later = await waiting_request(s, conversation_id=cid)
    assert 4.9 < _hours(later) <= 5


async def test_the_reminder_comes_before_the_expiry(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    r = await client.patch(
        f"/api/v1/orgs/{org_headers['X-Org-Id']}/settings",
        json={"action_expiry_hours": 24, "action_reminder_hours": 24},
        headers=org_headers,
    )
    assert (r.status_code, r.json()["error"]["code"]) == (400, "reminder_after_expiry")


async def test_what_is_sealed_is_what_is_hashed(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    cid = await _conversation_id(client, org_headers)
    async with get_sessionmaker()() as s:
        row = await waiting_request(
            s, conversation_id=cid, tool_input={"sql": "UPDATE t SET token = 'sk-live-123'"}
        )
        conv = await s.get(Conversation, cid)
        assert conv is not None
    payload = approvals_svc.unseal(row)
    assert payload["input"] == {"sql": "UPDATE t SET token = 'sk-live-123'"}
    assert approvals_svc.digest(payload) == row.input_sha256
    row.input_sha256 = "0" * 64
    with pytest.raises(ValueError, match="doesn't match"):
        approvals_svc.unseal(row)
