"""The approval round trip (task 3.8).

The flow is genuinely concurrent: a tool call blocks inside `can_use_tool`,
the `approval_required` event must reach the consumer *while* it is blocked,
a separate request resolves it, and only then does the turn continue. If the
event were queued behind the blocked driver — the obvious way to build it —
the client would never see the prompt and every approval would time out.

**These drive `chat.run_message` directly rather than the HTTP endpoint**, and
that is a deliberate limitation of the harness, not a shortcut: httpx's
`ASGITransport` runs the whole ASGI app to completion before exposing the
response stream (see `handle_async_request`), so no test through it can ever
observe an event *mid-turn*. Everything below the transport — the merged
queue in `Turn.stream`, the real permission callback, the registry, the
resolve service — is the real thing. The HTTP layer's incremental delivery is
verified live against a running uvicorn instead.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from app.agent.caps import CapabilityTool
from app.db.session import get_sessionmaker
from app.models.approval import ApprovalStatus
from app.services import approvals as approvals_svc
from app.services import chat as chat_svc
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.anyio


@pytest.fixture
def fake_sql(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace the SQL tools with one that records what it ran.

    The subject here is the approval handshake, not the database — and a real
    `sql_query` would need Postgres in the unit tier.
    """
    seen: dict[str, Any] = {"ran": []}

    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        seen["ran"].append(args.get("sql"))
        return {"content": [{"type": "text", "text": "1 row(s) affected"}]}

    def build(_assistant_id: uuid.UUID, _databases: list[Any]) -> list[CapabilityTool]:
        return [
            CapabilityTool(
                name="sql_query",
                description="stub",
                input_schema={"type": "object"},
                handler=handler,
                read_only=False,
            )
        ]

    monkeypatch.setattr("app.agent.options.build_sql_tools", build)
    monkeypatch.setattr("app.agent.options.build_mongo_tools", lambda *_a, **_k: [])
    return seen


async def _assistant_with_db(client: AsyncClient, headers: dict[str, str]) -> str:
    a = (await client.post("/api/v1/assistants", json={"name": "DB Bot"}, headers=headers)).json()
    # A real connection row whose credential allows writes and DDL: the router checks
    # the live credential before asking anyone (F-6/F-10), so a made-up id
    # would be refused without a prompt — correctly — and there would be
    # no approval to test. The SQL *tool* is still the stub above.
    r = await client.post(
        f"/api/v1/assistants/{a['id']}/db-connections",
        json={
            "name": "PG",
            "engine": "postgres",
            "host": "db",
            "database": "x",
            "permissions": {"read": True, "write": True, "ddl": True},
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    cfg = a["draft_config"]
    cfg["databases"] = [{"connection_id": r.json()["id"], "nl2sql": True, "expose_write": True}]
    r = await client.put(f"/api/v1/assistants/{a['id']}/draft-config", json=cfg, headers=headers)
    assert r.status_code == 200, r.text
    return str(a["id"])


async def _conversation(client: AsyncClient, headers: dict[str, str], aid: str) -> str:
    r = await client.post(f"/api/v1/assistants/{aid}/conversations", json={}, headers=headers)
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def _run_turn(conversation_id: str, text: str, events: list[Any]) -> None:
    """Consume a turn exactly as the SSE route does, into `events`."""
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        async for event in chat_svc.run_message(
            session, conversation_id=uuid.UUID(conversation_id), text=text
        ):
            events.append(event)


async def _await_prompt(events: list[Any], deadline_s: float = 10.0) -> Any:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + deadline_s
    while loop.time() < deadline:
        for e in events:
            if e.type == "approval_required":
                return e
        await asyncio.sleep(0.02)
    raise AssertionError(f"no approval_required arrived; saw {[e.type for e in events]}")


async def _resolve(
    client: AsyncClient, headers: dict[str, str], approval_id: str, decision: str
) -> Any:
    return await client.post(
        f"/api/v1/approvals/{approval_id}:resolve",
        json={"decision": decision},
        headers=headers,
    )


async def test_a_write_blocks_prompts_and_runs_once_approved(
    client: AsyncClient, org_headers: dict[str, str], fake_sql: dict[str, Any]
) -> None:
    aid = await _assistant_with_db(client, org_headers)
    cid = await _conversation(client, org_headers, aid)

    events: list[Any] = []
    turn = asyncio.create_task(_run_turn(cid, "sql: DELETE FROM sessions WHERE expired", events))

    prompt = await _await_prompt(events)
    # The reviewer sees the statement itself, not a paraphrase of it.
    assert prompt.rationale == "DELETE FROM sessions WHERE expired"
    assert prompt.risk == "high"
    assert prompt.expires_at

    # The turn is genuinely still blocked, and nothing has run.
    assert not turn.done()
    assert fake_sql["ran"] == []

    resolved = await _resolve(client, org_headers, prompt.approval_id, "approved")
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["status"] == "approved"

    await asyncio.wait_for(turn, timeout=15)
    assert [e.type for e in events][-1] == "done"
    assert fake_sql["ran"] == ["DELETE FROM sessions WHERE expired"]
    (result,) = [e for e in events if e.type == "tool_result"]
    assert result.status == "success"


async def test_a_denial_stops_the_statement_from_running(
    client: AsyncClient, org_headers: dict[str, str], fake_sql: dict[str, Any]
) -> None:
    aid = await _assistant_with_db(client, org_headers)
    cid = await _conversation(client, org_headers, aid)

    events: list[Any] = []
    turn = asyncio.create_task(_run_turn(cid, "sql: DROP TABLE users", events))
    prompt = await _await_prompt(events)

    await _resolve(client, org_headers, prompt.approval_id, "denied")
    await asyncio.wait_for(turn, timeout=15)

    assert fake_sql["ran"] == [], "a denied statement must never execute"
    (result,) = [e for e in events if e.type == "tool_result"]
    assert result.status == "denied"
    assert "declined" in result.output


async def test_a_read_is_never_prompted_for(
    client: AsyncClient, org_headers: dict[str, str], fake_sql: dict[str, Any]
) -> None:
    aid = await _assistant_with_db(client, org_headers)
    cid = await _conversation(client, org_headers, aid)

    events: list[Any] = []
    await _run_turn(cid, "sql: SELECT count(*) FROM users", events)

    assert "approval_required" not in [e.type for e in events]
    assert fake_sql["ran"] == ["SELECT count(*) FROM users"]


async def test_a_pending_approval_is_listable_for_a_reloaded_page(
    client: AsyncClient, org_headers: dict[str, str], fake_sql: dict[str, Any]
) -> None:
    """The SSE event only reaches whoever was watching; refreshing the tab
    must not orphan a decision that is still genuinely waiting."""
    aid = await _assistant_with_db(client, org_headers)
    cid = await _conversation(client, org_headers, aid)

    events: list[Any] = []
    turn = asyncio.create_task(_run_turn(cid, "sql: UPDATE users SET a = 1", events))
    prompt = await _await_prompt(events)

    listed = (
        await client.get(f"/api/v1/conversations/{cid}/approvals", headers=org_headers)
    ).json()["items"]
    assert [a["id"] for a in listed] == [prompt.approval_id]
    assert listed[0]["status"] == "pending"

    await _resolve(client, org_headers, prompt.approval_id, "denied")
    await asyncio.wait_for(turn, timeout=15)

    after = (await client.get(f"/api/v1/conversations/{cid}/approvals", headers=org_headers)).json()
    assert after["items"] == []


async def test_an_already_decided_approval_cannot_be_decided_again(
    client: AsyncClient, org_headers: dict[str, str], fake_sql: dict[str, Any]
) -> None:
    """By the time a second decision lands the tool has already run or not —
    recording it would claim something that never happened."""
    aid = await _assistant_with_db(client, org_headers)
    cid = await _conversation(client, org_headers, aid)

    events: list[Any] = []
    turn = asyncio.create_task(_run_turn(cid, "sql: DELETE FROM t", events))
    prompt = await _await_prompt(events)

    assert (await _resolve(client, org_headers, prompt.approval_id, "approved")).status_code == 200
    second = await _resolve(client, org_headers, prompt.approval_id, "denied")
    assert second.status_code == 400
    assert second.json()["error"]["code"] == "approval_not_pending"

    await asyncio.wait_for(turn, timeout=15)


async def test_a_user_outside_the_org_cannot_approve(
    client: AsyncClient, org_headers: dict[str, str], fake_sql: dict[str, Any]
) -> None:
    aid = await _assistant_with_db(client, org_headers)
    cid = await _conversation(client, org_headers, aid)

    events: list[Any] = []
    turn = asyncio.create_task(_run_turn(cid, "sql: DELETE FROM t", events))
    prompt = await _await_prompt(events)

    outsider = await client.post(
        "/api/v1/auth/register",
        json={"email": "outsider-approve@example.com", "password": "supersecret", "name": "x"},
    )
    bob = {"Authorization": f"Bearer {outsider.json()['access_token']}"}
    denied = await _resolve(client, bob, prompt.approval_id, "approved")
    # 404, not 403: an outsider should not learn the id exists.
    assert denied.status_code == 404

    await _resolve(client, org_headers, prompt.approval_id, "denied")
    await asyncio.wait_for(turn, timeout=15)
    assert fake_sql["ran"] == []


async def test_a_timeout_expires_the_row_and_denies(
    client: AsyncClient,
    org_headers: dict[str, str],
    fake_sql: dict[str, Any],
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nobody answering must never become an implicit yes."""
    monkeypatch.setattr("app.services.chat.settings.approval_timeout_s", 0.3)
    aid = await _assistant_with_db(client, org_headers)
    cid = await _conversation(client, org_headers, aid)

    events: list[Any] = []
    await asyncio.wait_for(_run_turn(cid, "sql: DELETE FROM t", events), timeout=20)

    assert fake_sql["ran"] == []
    (result,) = [e for e in events if e.type == "tool_result"]
    assert result.status == "denied"

    prompt = next(e for e in events if e.type == "approval_required")
    row = await approvals_svc.get(db_session, uuid.UUID(prompt.approval_id))
    assert row.status is ApprovalStatus.expired
