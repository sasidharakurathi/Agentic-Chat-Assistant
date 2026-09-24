"""Archiving a conversation actually does something (F-5).

`DELETE /conversations/{id}` set `status = archived` and returned
"archived" — but the list had no status filter, so the conversation came
straight back on reload, and it still accepted new messages (and so still ran
the agent, with its tools, and spent money). An archive now leaves the list
and becomes read-only. A direct link still reads it: it is archived, not
erased.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.anyio


async def _archived(client: AsyncClient, headers: dict[str, str]) -> tuple[str, str, str]:
    aid = (await client.post("/api/v1/assistants", json={"name": "A"}, headers=headers)).json()[
        "id"
    ]
    keep = await client.post(f"/api/v1/assistants/{aid}/conversations", json={}, headers=headers)
    gone = await client.post(f"/api/v1/assistants/{aid}/conversations", json={}, headers=headers)
    r = await client.delete(f"/api/v1/conversations/{gone.json()['id']}", headers=headers)
    assert r.status_code == 200, r.text
    return aid, gone.json()["id"], keep.json()["id"]


async def test_an_archived_conversation_leaves_the_list(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    aid, gone, keep = await _archived(client, org_headers)
    listed = await client.get(f"/api/v1/assistants/{aid}/conversations", headers=org_headers)
    ids = {c["id"] for c in listed.json()["items"]}
    assert keep in ids
    assert gone not in ids, "it used to come straight back on reload"


async def test_an_archived_conversation_accepts_no_new_messages(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    """Otherwise an "archived" thread can still run the agent — tools and
    spend included."""
    _, gone, _ = await _archived(client, org_headers)
    r = await client.post(
        f"/api/v1/conversations/{gone}/messages", json={"text": "hello?"}, headers=org_headers
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "conversation_archived"


async def test_a_direct_link_still_reads_it(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    _, gone, _ = await _archived(client, org_headers)
    r = await client.get(f"/api/v1/conversations/{gone}", headers=org_headers)
    assert r.status_code == 200
    assert r.json()["status"] == "archived"
