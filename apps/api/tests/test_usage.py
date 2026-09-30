"""GET /orgs/{id}/usage — rollups over usage_events, written one per turn by
chat.run_message (see test_chat.py for the turn machinery itself)."""

from __future__ import annotations

from httpx import AsyncClient


async def _new_assistant(client: AsyncClient, org_headers: dict[str, str], name: str) -> str:
    r = await client.post("/api/v1/assistants", json={"name": name}, headers=org_headers)
    return r.json()["id"]


async def _chat_once(
    client: AsyncClient, org_headers: dict[str, str], assistant_id: str, text: str
) -> None:
    conv = await client.post(
        f"/api/v1/assistants/{assistant_id}/conversations", json={}, headers=org_headers
    )
    cid = conv.json()["id"]
    async with client.stream(
        "POST",
        f"/api/v1/conversations/{cid}/messages",
        json={"text": text},
        headers=org_headers,
    ) as resp:
        assert resp.status_code == 200
        async for _ in resp.aiter_lines():
            pass


async def test_usage_rollup_by_assistant_and_model(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    a1 = await _new_assistant(client, org_headers, "Usage A")
    a2 = await _new_assistant(client, org_headers, "Usage B")
    await _chat_once(client, org_headers, a1, "hello from a1")
    await _chat_once(client, org_headers, a1, "again from a1")
    await _chat_once(client, org_headers, a2, "hello from a2")

    org_id = org_headers["X-Org-Id"]

    by_assistant = (
        await client.get(f"/api/v1/orgs/{org_id}/usage?group_by=assistant", headers=org_headers)
    ).json()
    assert by_assistant["group_by"] == "assistant"
    rows_by_key = {r["group_key"]: r for r in by_assistant["rows"]}
    assert rows_by_key[a1]["event_count"] == 2
    assert rows_by_key[a2]["event_count"] == 1
    assert rows_by_key[a1]["tokens_out"] > 0

    by_model = (
        await client.get(f"/api/v1/orgs/{org_id}/usage?group_by=model", headers=org_headers)
    ).json()
    assert by_model["group_by"] == "model"
    assert sum(r["event_count"] for r in by_model["rows"]) == 3


async def test_usage_rollup_requires_org_membership(client: AsyncClient) -> None:
    async def signup(email: str) -> dict[str, str]:
        r = await client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": "supersecret", "name": "x"},
        )
        h = {"Authorization": f"Bearer {r.json()['access_token']}"}
        me = await client.get("/api/v1/auth/me", headers=h)
        return {**h, "X-Org-Id": me.json()["memberships"][0]["org_id"]}

    alice = await signup("alice-usage@example.com")
    bob = await signup("bob-usage@example.com")

    resp = await client.get(f"/api/v1/orgs/{alice['X-Org-Id']}/usage", headers=bob)
    assert resp.status_code == 404


async def test_the_dashboard_rollups_name_what_they_group(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    """Task 5.7: assistant names, conversation titles, and top-N."""
    a1 = await _new_assistant(client, org_headers, "Named A")
    a2 = await _new_assistant(client, org_headers, "Named B")
    await _chat_once(client, org_headers, a1, "hello from a1 " * 50)
    await _chat_once(client, org_headers, a2, "hi")
    org = org_headers["X-Org-Id"]
    base = f"/api/v1/orgs/{org}/usage"

    by_assistant = (
        await client.get(base, params={"group_by": "assistant"}, headers=org_headers)
    ).json()["rows"]
    assert {r["group_key"]: r["label"] for r in by_assistant} == {a1: "Named A", a2: "Named B"}

    by_model = (await client.get(base, params={"group_by": "model"}, headers=org_headers)).json()
    assert all(r["label"] == r["group_key"] for r in by_model["rows"])

    top = (
        await client.get(base, params={"group_by": "conversation", "limit": 1}, headers=org_headers)
    ).json()["rows"]
    assert len(top) == 1, "top-N"
    assert top[0]["label"] and top[0]["label"] != "New conversation", "the auto title"
    assert top[0]["detail"] == "Named A", "whose conversation"
    assert float(top[0]["cost_usd"]) == max(float(r["cost_usd"]) for r in by_assistant)
