"""End-to-end chat over SSE, driven by the offline FakeDriver (APP_ENV=test)."""

from __future__ import annotations

import json

from httpx import AsyncClient


async def _new_assistant(
    client: AsyncClient, org_headers: dict[str, str], config_patch: dict | None = None
) -> str:
    a = (
        await client.post("/api/v1/assistants", json={"name": "Chatty"}, headers=org_headers)
    ).json()
    if config_patch:
        cfg = a["draft_config"]
        cfg.update(config_patch)
        await client.put(
            f"/api/v1/assistants/{a['id']}/draft-config", json=cfg, headers=org_headers
        )
    return a["id"]


async def _new_conversation(
    client: AsyncClient, org_headers: dict[str, str], assistant_id: str
) -> str:
    r = await client.post(
        f"/api/v1/assistants/{assistant_id}/conversations", json={}, headers=org_headers
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _send(
    client: AsyncClient, org_headers: dict[str, str], conv_id: str, text: str
) -> list[dict]:
    events: list[dict] = []
    async with client.stream(
        "POST",
        f"/api/v1/conversations/{conv_id}/messages",
        json={"text": text},
        headers=org_headers,
    ) as resp:
        assert resp.status_code == 200, resp.text
        async for line in resp.aiter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


async def test_basic_turn_streams_tokens_and_persists(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    aid = await _new_assistant(client, org_headers)
    cid = await _new_conversation(client, org_headers, aid)

    events = await _send(client, org_headers, cid, "hello there")
    kinds = [e["type"] for e in events]
    assert "token" in kinds
    assert "usage" in kinds
    assert kinds[-1] == "done"

    detail = (await client.get(f"/api/v1/conversations/{cid}", headers=org_headers)).json()
    roles = [m["role"] for m in detail["messages"]]
    assert roles == ["user", "assistant"]
    assert "hello there" in detail["messages"][1]["content"]
    assert float(detail["cost_usd"]) >= 0.0
    assert detail["token_usage"]["out"] > 0


async def test_calculator_tool_roundtrip(client: AsyncClient, org_headers: dict[str, str]) -> None:
    aid = await _new_assistant(client, org_headers, {"tools": {"calculator": {"enabled": True}}})
    cid = await _new_conversation(client, org_headers, aid)

    events = await _send(client, org_headers, cid, "please calculate 21 + 21")
    by_kind = {e["type"] for e in events}
    assert {"tool_call", "tool_result"} <= by_kind
    tool_result = next(e for e in events if e["type"] == "tool_result")
    assert "= 42.0" in tool_result["output"]


async def test_budget_limit_aborts_turn(client: AsyncClient, org_headers: dict[str, str]) -> None:
    aid = await _new_assistant(
        client,
        org_headers,
        {"models": {"main": {"model": "claude-sonnet-5", "max_budget_usd": 0.0000001}}},
    )
    cid = await _new_conversation(client, org_headers, aid)

    events = await _send(client, org_headers, cid, "spend my whole budget please")
    assert any(e["type"] == "error" and e["code"] == "budget_exceeded" for e in events)

    # a follow-up is rejected immediately (already over budget)
    events2 = await _send(client, org_headers, cid, "again")
    assert events2[0]["type"] == "error" and events2[0]["code"] == "budget_exceeded"


async def test_session_id_resumes_across_turns(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    """FakeDriver echoes back whatever ``session_id`` it's given (or mints one
    if there wasn't one yet) — so if chat.run_message correctly threads the
    conversation's sdk_session_id into the second turn, both turns' usage
    events carry the *same* id."""
    aid = await _new_assistant(client, org_headers)
    cid = await _new_conversation(client, org_headers, aid)

    events1 = await _send(client, org_headers, cid, "first turn")
    session_id_1 = next(e for e in events1 if e["type"] == "usage")["sdk_session_id"]
    assert session_id_1

    events2 = await _send(client, org_headers, cid, "second turn")
    session_id_2 = next(e for e in events2 if e["type"] == "usage")["sdk_session_id"]
    assert session_id_2 == session_id_1


async def test_cross_tenant_conversation_is_404(client: AsyncClient) -> None:
    async def signup(email: str) -> dict[str, str]:
        r = await client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": "supersecret", "name": "x"},
        )
        h = {"Authorization": f"Bearer {r.json()['access_token']}"}
        me = await client.get("/api/v1/auth/me", headers=h)
        return {**h, "X-Org-Id": me.json()["memberships"][0]["org_id"]}

    alice = await signup("alice2@example.com")
    bob = await signup("bob2@example.com")
    aid = await _new_assistant(client, alice)
    cid = await _new_conversation(client, alice, aid)

    resp = await client.get(f"/api/v1/conversations/{cid}", headers=bob)
    assert resp.status_code == 404
