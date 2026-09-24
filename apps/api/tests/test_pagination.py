"""Every list endpoint is paginated, by keyset (plan §8; audit "never checked").

What makes keyset pagination correct, and therefore what is tested here:
- walking every page yields every row exactly once, in order;
- that holds when timestamps TIE at a page boundary (the id tie-breaker);
- that holds when a row is inserted mid-walk (offset paging repeats one);
- a cursor is not a capability: it cannot reach another tenant's rows.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from app.db.pagination import InvalidCursor, decode_cursor, encode_cursor
from app.db.session import get_sessionmaker
from app.models.conversation import Conversation, Message, MessageRole
from httpx import AsyncClient
from sqlalchemy import update

pytestmark = pytest.mark.anyio


def test_a_cursor_round_trips_every_key_type() -> None:
    values = [datetime(2026, 1, 2, 3, 4, 5, 6, tzinfo=UTC), uuid.uuid4(), 7, "x"]
    assert decode_cursor(encode_cursor(values), 4) == values


@pytest.mark.parametrize("bad", ["", "not-base64!!", encode_cursor([1])])
def test_a_bad_or_foreign_cursor_is_refused(bad: str) -> None:
    with pytest.raises(InvalidCursor):
        decode_cursor(bad, 2)


async def _assistant(client: AsyncClient, headers: dict[str, str]) -> str:
    r = await client.post("/api/v1/assistants", json={"name": "Pager"}, headers=headers)
    return str(r.json()["id"])


async def _conversations(
    client: AsyncClient, headers: dict[str, str], aid: str, n: int
) -> list[str]:
    ids = []
    for i in range(n):
        r = await client.post(
            f"/api/v1/assistants/{aid}/conversations", json={"title": f"c{i}"}, headers=headers
        )
        ids.append(r.json()["id"])
    return ids


async def _walk(
    client: AsyncClient, headers: dict[str, str], url: str, limit: int
) -> list[list[Any]]:
    pages: list[list[Any]] = []
    cursor: str | None = None
    while True:
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        r = await client.get(url, params=params, headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        pages.append(body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            return pages
        assert len(pages) < 50, "the walk never ended"


async def test_walking_every_page_yields_every_row_once(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    aid = await _assistant(client, org_headers)
    made = await _conversations(client, org_headers, aid, 7)
    pages = await _walk(client, org_headers, f"/api/v1/assistants/{aid}/conversations", 3)
    assert [len(p) for p in pages] == [3, 3, 1]
    seen = [c["id"] for p in pages for c in p]
    assert sorted(seen) == sorted(made) and len(set(seen)) == 7


async def test_tied_timestamps_at_a_page_boundary_lose_nothing(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    """Every row gets the SAME created_at. Ordering by created_at alone would
    make page boundaries arbitrary; the id tie-breaker keeps it a total order."""
    aid = await _assistant(client, org_headers)
    made = await _conversations(client, org_headers, aid, 9)
    async with get_sessionmaker()() as s:
        await s.execute(
            update(Conversation)
            .where(Conversation.assistant_id == uuid.UUID(aid))
            .values(created_at=datetime(2026, 1, 1, tzinfo=UTC))
        )
        await s.commit()
    pages = await _walk(client, org_headers, f"/api/v1/assistants/{aid}/conversations", 2)
    seen = [c["id"] for p in pages for c in p]
    assert len(seen) == 9 and set(seen) == set(made)


async def test_a_row_inserted_mid_walk_does_not_repeat_one(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    """With OFFSET, a new row pushes the last row of page 1 onto page 2."""
    aid = await _assistant(client, org_headers)
    await _conversations(client, org_headers, aid, 4)
    url = f"/api/v1/assistants/{aid}/conversations"
    first = (await client.get(url, params={"limit": 2}, headers=org_headers)).json()
    await _conversations(client, org_headers, aid, 1)
    second = (
        await client.get(
            url, params={"limit": 2, "cursor": first["next_cursor"]}, headers=org_headers
        )
    ).json()
    ids_1 = {c["id"] for c in first["items"]}
    ids_2 = {c["id"] for c in second["items"]}
    assert not ids_1 & ids_2
    assert len(ids_2) == 2


async def test_a_bad_cursor_is_a_400_and_limits_are_bounded(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    aid = await _assistant(client, org_headers)
    url = f"/api/v1/assistants/{aid}/conversations"
    r = await client.get(url, params={"cursor": "garbage"}, headers=org_headers)
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_cursor"
    for limit in (0, 201):
        assert (
            await client.get(url, params={"limit": limit}, headers=org_headers)
        ).status_code == 422


async def test_a_cursor_cannot_reach_another_assistants_rows(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    mine = await _assistant(client, org_headers)
    other = await _assistant(client, org_headers)
    await _conversations(client, org_headers, mine, 3)
    theirs = set(await _conversations(client, org_headers, other, 3))
    page = (
        await client.get(
            f"/api/v1/assistants/{other}/conversations", params={"limit": 1}, headers=org_headers
        )
    ).json()
    rest = (
        await client.get(
            f"/api/v1/assistants/{mine}/conversations",
            params={"cursor": page["next_cursor"]},
            headers=org_headers,
        )
    ).json()
    assert not {c["id"] for c in rest["items"]} & theirs


async def test_the_conversation_detail_no_longer_inlines_all_history(
    client: AsyncClient, org_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.api.routes.conversations.DETAIL_MESSAGES", 2)
    aid = await _assistant(client, org_headers)
    (cid,) = await _conversations(client, org_headers, aid, 1)
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    async with get_sessionmaker()() as s:
        conv = await s.get(Conversation, uuid.UUID(cid))
        assert conv is not None
        for i in range(5):
            s.add(
                Message(
                    conversation_id=conv.id,
                    org_id=conv.org_id,
                    role=MessageRole.user,
                    content=f"m{i}",
                    created_at=t0 + timedelta(seconds=i),
                )
            )
        await s.commit()

    detail = (await client.get(f"/api/v1/conversations/{cid}", headers=org_headers)).json()
    assert [m["content"] for m in detail["messages"]] == ["m3", "m4"], "latest, oldest-first"
    history = [m["content"] for m in detail["messages"]]
    cursor = detail["messages_next_cursor"]
    while cursor:
        page = (
            await client.get(
                f"/api/v1/conversations/{cid}/messages",
                params={"cursor": cursor, "limit": 2},
                headers=org_headers,
            )
        ).json()
        history = [m["content"] for m in page["items"]] + history  # prepend as-is
        cursor = page["next_cursor"]
    assert history == ["m0", "m1", "m2", "m3", "m4"]


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/orgs",
        "/api/v1/assistants",
        "/api/v1/assistants/{aid}/versions",
        "/api/v1/assistants/{aid}/conversations",
        "/api/v1/assistants/{aid}/data-sources",
        "/api/v1/assistants/{aid}/db-connections",
        "/api/v1/conversations/{cid}/approvals",
        "/api/v1/conversations/{cid}/messages",
        "/api/v1/orgs/{org}/members",
        "/api/v1/orgs/{org}/audit-log",
    ],
)
async def test_every_list_endpoint_returns_a_page(
    client: AsyncClient, org_headers: dict[str, str], path: str
) -> None:
    aid = await _assistant(client, org_headers)
    (cid,) = await _conversations(client, org_headers, aid, 1)
    url = path.format(aid=aid, cid=cid, org=org_headers["X-Org-Id"])
    r = await client.get(url, params={"limit": 1}, headers=org_headers)
    assert r.status_code == 200, r.text
    assert set(r.json()) == {"items", "next_cursor"}
    assert len(r.json()["items"]) <= 1
