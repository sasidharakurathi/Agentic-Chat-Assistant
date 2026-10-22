"""The request round trip (task 3.8; durable since Phase 9.1, plan D17).

A write no longer blocks its turn: the router stores it as a request with
the exact input sealed, announces it (`approval_required`), and the turn
ends. The person decides on the card, naming the hash of what they saw; the
executor runs it once and posts how it went into the conversation, which
the next turn is told.

These are previews, so the builder decides their own test writes (your
choice for Phase 9.1). The approver's side, in live Chat, is in
`test_requests.py`.

**These drive `chat.run_message` directly rather than the HTTP endpoint**:
httpx's `ASGITransport` runs the whole ASGI app to completion before
exposing the response stream, so no test through it can observe an event
mid-turn. Everything below the transport is the real thing.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from app.agent.caps import CapabilityTool
from app.db.session import get_sessionmaker
from app.models.approval import Approval, ApprovalStatus, ApprovalTier
from app.models.conversation import Conversation, Message, MessageRole
from app.services import chat as chat_svc
from app.services import executor
from httpx import AsyncClient
from sqlalchemy import select
from tests.pending import decide

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

    def build(
        _assistant_id: uuid.UUID, _databases: list[Any], _asker: Any = None
    ) -> list[CapabilityTool]:
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
    # The executor builds the version's tools itself (Phase 9.1).
    monkeypatch.setattr("app.agent.caps_sql.build_sql_tools", build)
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
    r = await client.post(
        f"/api/v1/assistants/{aid}/conversations", json={"mode": "preview"}, headers=headers
    )
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
    """Decide as the card does: naming what was shown."""
    return await decide(client, headers, approval_id, decision)


async def _row(approval_id: str) -> Approval:
    async with get_sessionmaker()() as s:
        row = await s.get(Approval, uuid.UUID(approval_id))
        assert row is not None
        return row


async def _notices(conversation_id: str) -> list[str]:
    async with get_sessionmaker()() as s:
        return [
            m.content
            for m in (
                await s.scalars(
                    select(Message)
                    .where(
                        Message.conversation_id == uuid.UUID(conversation_id),
                        Message.role == MessageRole.system,
                    )
                    .order_by(Message.created_at)
                )
            ).all()
        ]


async def test_a_write_is_stored_and_the_turn_ends_without_running_it(
    client: AsyncClient, org_headers: dict[str, str], fake_sql: dict[str, Any]
) -> None:
    aid = await _assistant_with_db(client, org_headers)
    cid = await _conversation(client, org_headers, aid)

    events: list[Any] = []
    await asyncio.wait_for(
        _run_turn(cid, "sql: DELETE FROM sessions WHERE expired", events), timeout=15
    )
    prompt = await _await_prompt(events)
    # The person sees the statement itself, not a paraphrase of it.
    assert prompt.rationale == "DELETE FROM sessions WHERE expired"
    assert (prompt.risk, prompt.tier, prompt.status) == ("high", "asker", "pending")
    assert prompt.reference.startswith("R-") and prompt.input_sha256
    assert [e.type for e in events][-1] == "done", "nothing waits inside the turn"
    assert fake_sql["ran"] == []

    row = await _row(prompt.approval_id)
    assert row.status is ApprovalStatus.pending and row.tier is ApprovalTier.asker
    assert row.preview is True
    assert row.sealed_ciphertext is not None, "the exact input is kept, sealed"
    assert b"DELETE" not in row.sealed_ciphertext


async def test_confirming_runs_it_once_and_says_so_in_the_thread(
    client: AsyncClient, org_headers: dict[str, str], fake_sql: dict[str, Any]
) -> None:
    aid = await _assistant_with_db(client, org_headers)
    cid = await _conversation(client, org_headers, aid)
    events: list[Any] = []
    await _run_turn(cid, "sql: DELETE FROM sessions WHERE expired", events)
    prompt = await _await_prompt(events)

    resolved = await _resolve(client, org_headers, prompt.approval_id, "approved")
    assert resolved.status_code == 200, resolved.text
    await executor.drain()
    assert fake_sql["ran"] == ["DELETE FROM sessions WHERE expired"]
    row = await _row(prompt.approval_id)
    assert row.status is ApprovalStatus.succeeded
    assert row.result == "1 row(s) affected"
    assert row.sealed_ciphertext is None, "wiped once it ran"
    assert any("was done" in n for n in await _notices(cid))

    # Run once: another hand-over finds nothing to claim.
    assert await executor.execute(uuid.UUID(prompt.approval_id)) is None
    assert fake_sql["ran"] == ["DELETE FROM sessions WHERE expired"]


async def test_the_next_turn_is_told_how_it_ended(
    client: AsyncClient, org_headers: dict[str, str], fake_sql: dict[str, Any]
) -> None:
    """The model's own session still thinks it is waiting."""
    aid = await _assistant_with_db(client, org_headers)
    cid = await _conversation(client, org_headers, aid)
    events: list[Any] = []
    await _run_turn(cid, "sql: DELETE FROM t", events)
    prompt = await _await_prompt(events)
    await _resolve(client, org_headers, prompt.approval_id, "approved")
    await executor.drain()

    async with get_sessionmaker()() as s:
        conv = await s.get(Conversation, uuid.UUID(cid))
        assert conv is not None
        notes = await chat_svc._request_notes(s, conv)
        assert prompt.reference in notes and "was done" in notes
        assert "from the platform, not the person" in notes
        assert await chat_svc._request_notes(s, conv) == "", "told once"


async def test_a_denial_runs_nothing(
    client: AsyncClient, org_headers: dict[str, str], fake_sql: dict[str, Any]
) -> None:
    aid = await _assistant_with_db(client, org_headers)
    cid = await _conversation(client, org_headers, aid)
    events: list[Any] = []
    await _run_turn(cid, "sql: DROP TABLE users", events)
    prompt = await _await_prompt(events)

    assert (await _resolve(client, org_headers, prompt.approval_id, "denied")).status_code == 200
    await executor.drain()
    assert fake_sql["ran"] == [], "a declined statement never runs"
    row = await _row(prompt.approval_id)
    assert row.status is ApprovalStatus.denied and row.sealed_ciphertext is None
    assert any("was declined" in n for n in await _notices(cid))


async def test_a_read_is_never_stored(
    client: AsyncClient, org_headers: dict[str, str], fake_sql: dict[str, Any]
) -> None:
    aid = await _assistant_with_db(client, org_headers)
    cid = await _conversation(client, org_headers, aid)

    events: list[Any] = []
    await _run_turn(cid, "sql: SELECT count(*) FROM users", events)

    assert "approval_required" not in [e.type for e in events]
    assert fake_sql["ran"] == ["SELECT count(*) FROM users"]


async def test_requests_are_listed_for_a_reloaded_page(
    client: AsyncClient, org_headers: dict[str, str], fake_sql: dict[str, Any]
) -> None:
    """The event only reaches whoever was watching; a reload reads them back:
    those waiting by default, every one with `all=true`."""
    aid = await _assistant_with_db(client, org_headers)
    cid = await _conversation(client, org_headers, aid)
    events: list[Any] = []
    await _run_turn(cid, "sql: UPDATE users SET a = 1", events)
    prompt = await _await_prompt(events)

    url = f"/api/v1/conversations/{cid}/approvals"
    listed = (await client.get(url, headers=org_headers)).json()["items"]
    assert [(a["id"], a["status"]) for a in listed] == [(prompt.approval_id, "pending")]
    assert listed[0]["reference"] == prompt.reference

    await _resolve(client, org_headers, prompt.approval_id, "denied")
    assert (await client.get(url, headers=org_headers)).json()["items"] == []
    everything = (await client.get(f"{url}?all=true", headers=org_headers)).json()["items"]
    assert [a["status"] for a in everything] == ["denied"]


async def test_an_already_decided_request_cannot_be_decided_again(
    client: AsyncClient, org_headers: dict[str, str], fake_sql: dict[str, Any]
) -> None:
    aid = await _assistant_with_db(client, org_headers)
    cid = await _conversation(client, org_headers, aid)
    events: list[Any] = []
    await _run_turn(cid, "sql: DELETE FROM t", events)
    prompt = await _await_prompt(events)

    assert (await _resolve(client, org_headers, prompt.approval_id, "approved")).status_code == 200
    second = await _resolve(client, org_headers, prompt.approval_id, "denied")
    assert (second.status_code, second.json()["error"]["code"]) == (400, "approval_not_pending")
    await executor.drain()


async def test_a_user_outside_the_org_cannot_decide(
    client: AsyncClient, org_headers: dict[str, str], fake_sql: dict[str, Any]
) -> None:
    aid = await _assistant_with_db(client, org_headers)
    cid = await _conversation(client, org_headers, aid)
    events: list[Any] = []
    await _run_turn(cid, "sql: DELETE FROM t", events)
    prompt = await _await_prompt(events)

    outsider = await client.post(
        "/api/v1/auth/register",
        json={"email": "outsider-approve@example.com", "password": "supersecret", "name": "x"},
    )
    bob = {"Authorization": f"Bearer {outsider.json()['access_token']}"}
    # 404, not 403: an outsider should not learn the id exists.
    assert (await _resolve(client, bob, prompt.approval_id, "approved")).status_code == 404
    assert fake_sql["ran"] == []


async def test_the_asker_can_withdraw_what_still_waits(
    client: AsyncClient, org_headers: dict[str, str], fake_sql: dict[str, Any]
) -> None:
    aid = await _assistant_with_db(client, org_headers)
    cid = await _conversation(client, org_headers, aid)
    events: list[Any] = []
    await _run_turn(cid, "sql: DELETE FROM t", events)
    prompt = await _await_prompt(events)

    url = f"/api/v1/approvals/{prompt.approval_id}:withdraw"
    r = await client.post(url, headers=org_headers)
    assert (r.status_code, r.json()["status"]) == (200, "cancelled")
    again = await client.post(url, headers=org_headers)
    assert (again.status_code, again.json()["error"]["code"]) == (400, "approval_not_pending")
    assert fake_sql["ran"] == []
    assert any("was withdrawn" in n for n in await _notices(cid))
