"""Org isolation, structurally and per role (task 0.7).

- `app.db.tenancy`: once a request has bound its org, every ORM query on an
  org-owned table is limited to that org, whether or not it says so itself.
- A route whose query "forgets" its org filter still cannot return another
  org's rows.
- Who may do what, role by role, across the assistant, source, connection,
  usage and audit routes (there was one role assertion in the whole suite).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from app.api.errors import NotFound
from app.db.session import get_sessionmaker
from app.db.tenancy import bind_org, org_owned_models
from app.models.assistant import Assistant
from app.models.conversation import Conversation, Message
from app.models.membership import Membership
from app.models.rag import Chunk, DataSource
from httpx import AsyncClient
from sqlalchemy import select, update
from tests.test_orgs import Actor, _register
from tests.test_rbac_escalation import Team, _team

pytestmark = pytest.mark.anyio


async def _two_orgs(client: AsyncClient) -> tuple[Actor, str, str, Actor, str, str]:
    """Two users, each with an org holding one assistant."""
    out: list[Any] = []
    for email in ("alice@example.com", "bob@example.com"):
        actor = await _register(client, email)
        org = (
            await client.post("/api/v1/orgs", json={"name": email}, headers=actor.headers)
        ).json()
        h = {**actor.headers, "X-Org-Id": org["id"]}
        a = (await client.post("/api/v1/assistants", json={"name": email}, headers=h)).json()
        out += [actor, org["id"], a["id"]]
    return tuple(out)  # type: ignore[return-value]


def test_every_tenant_table_is_covered() -> None:
    names = {m.__tablename__ for m in org_owned_models()}
    for table in (
        "assistants",
        "conversations",
        "messages",
        "runs",
        "tool_calls",
        "data_sources",
        "documents",
        "chunks",
        "db_connections",
        "mcp_servers",
        "approvals",
        "usage_events",
        "memberships",
    ):
        assert table in names, table


async def test_a_bound_session_sees_only_its_org(client: AsyncClient) -> None:
    _, org_a, aid_a, _, _, aid_b = await _two_orgs(client)
    async with get_sessionmaker()() as s:
        everything = set(await s.scalars(select(Assistant.id)))
        assert {uuid.UUID(aid_a), uuid.UUID(aid_b)} <= everything, "unbound: no filter"

        bind_org(s, uuid.UUID(org_a))
        assert set(await s.scalars(select(Assistant.id))) == {uuid.UUID(aid_a)}
        assert await s.get(Assistant, uuid.UUID(aid_b)) is None
        # Joins are covered too, on every org-owned table in them.
        joined = await s.scalars(
            select(Membership.org_id).join(Assistant, Assistant.org_id == Membership.org_id)
        )
        assert set(joined) == {uuid.UUID(org_a)}
        # A deliberate cross-org query says so.
        wide = set(await s.scalars(select(Assistant.id).execution_options(all_orgs=True)))
        assert uuid.UUID(aid_b) in wide


async def test_a_bound_session_cannot_write_to_another_org(client: AsyncClient) -> None:
    _, org_a, _, _, _, aid_b = await _two_orgs(client)
    async with get_sessionmaker()() as s:
        bind_org(s, uuid.UUID(org_a))
        result = await s.execute(
            update(Assistant).where(Assistant.id == uuid.UUID(aid_b)).values(name="pwned")
        )
        await s.commit()
        assert result.rowcount == 0  # type: ignore[attr-defined]
    async with get_sessionmaker()() as s:
        b = await s.get(Assistant, uuid.UUID(aid_b))
        assert b is not None and b.name != "pwned"


async def test_one_request_cannot_bind_two_orgs() -> None:
    async with get_sessionmaker()() as s:
        bind_org(s, uuid.uuid4())
        with pytest.raises(NotFound):
            bind_org(s, uuid.uuid4())


async def test_a_route_that_forgets_its_org_filter_still_cannot_leak(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The data-source listing's query, with its assistant filter removed, the
    kind of slip a convention does not catch. Bob's source must not appear in
    Alice's listing."""
    from app.db.pagination import keyset_page
    from app.services import data_sources as svc

    alice, org_a, aid_a, bob, org_b, aid_b = await _two_orgs(client)
    for actor, org, aid in ((alice, org_a, aid_a), (bob, org_b, aid_b)):
        r = await client.post(
            f"/api/v1/assistants/{aid}/data-sources",
            json={"type": "text", "name": f"secret of {actor.user_id}", "text": "x"},
            headers={**actor.headers, "X-Org-Id": org},
        )
        assert r.status_code == 201, r.text

    async def forgetful(session: Any, _aid: uuid.UUID, **kw: Any) -> Any:
        return await keyset_page(
            session, select(DataSource), [DataSource.created_at, DataSource.id], **kw
        )

    monkeypatch.setattr(svc, "page_for_assistant", forgetful)
    listing = await client.get(
        f"/api/v1/assistants/{aid_a}/data-sources",
        headers={**alice.headers, "X-Org-Id": org_a},
    )
    names = [row["name"] for row in listing.json()["items"]]
    assert names == [f"secret of {alice.user_id}"]


async def test_chat_and_rag_tables_are_scoped_too(client: AsyncClient) -> None:
    alice, org_a, aid_a, bob, org_b, aid_b = await _two_orgs(client)
    for actor, org, aid in ((alice, org_a, aid_a), (bob, org_b, aid_b)):
        h = {**actor.headers, "X-Org-Id": org}
        conv = await client.post(f"/api/v1/assistants/{aid}/conversations", json={}, headers=h)
        assert conv.status_code == 201
    async with get_sessionmaker()() as s:
        bind_org(s, uuid.UUID(org_a))
        orgs = set(await s.scalars(select(Conversation.org_id)))
        assert orgs == {uuid.UUID(org_a)}
        assert all(m.org_id == uuid.UUID(org_a) for m in await s.scalars(select(Message)))
        assert list(await s.scalars(select(Chunk).where(Chunk.org_id == uuid.UUID(org_b)))) == []


# ── per-role matrix ──────────────────────────────────────────────────

ROLES = ("owner", "admin", "member", "outsider")


async def _actors(client: AsyncClient) -> tuple[Team, dict[str, dict[str, str]], str]:
    team = await _team(client)
    outsider = await _register(client, "outsider@example.com")
    org = (
        await client.post("/api/v1/orgs", json={"name": "Elsewhere"}, headers=outsider.headers)
    ).json()
    headers = {
        "owner": team.h(team.owner),
        "admin": team.h(team.admin),
        "member": team.h(team.member),
        # A real user with their own org, not a member of this one.
        "outsider": {**outsider.headers, "X-Org-Id": org["id"]},
    }
    a = (
        await client.post("/api/v1/assistants", json={"name": "Owned"}, headers=headers["owner"])
    ).json()
    return team, headers, a["id"]


@pytest.mark.parametrize(
    ("role", "expected"),
    [("owner", 200), ("admin", 200), ("member", 200), ("outsider", 404)],
)
async def test_reading_an_assistant(client: AsyncClient, role: str, expected: int) -> None:
    _, h, aid = await _actors(client)
    assert (await client.get(f"/api/v1/assistants/{aid}", headers=h[role])).status_code == expected


@pytest.mark.parametrize(
    ("role", "expected"),
    [("owner", 200), ("admin", 200), ("member", 403), ("outsider", 404)],
)
async def test_editing_someone_elses_assistant(
    client: AsyncClient, role: str, expected: int
) -> None:
    _, h, aid = await _actors(client)
    r = await client.patch(f"/api/v1/assistants/{aid}", json={"name": "Renamed"}, headers=h[role])
    assert r.status_code == expected, r.text


@pytest.mark.parametrize(
    ("role", "expected"),
    [("owner", 201), ("admin", 201), ("member", 403), ("outsider", 404)],
)
async def test_adding_a_source_to_someone_elses_assistant(
    client: AsyncClient, role: str, expected: int
) -> None:
    _, h, aid = await _actors(client)
    r = await client.post(
        f"/api/v1/assistants/{aid}/data-sources",
        json={"type": "text", "name": "notes", "text": "hello"},
        headers=h[role],
    )
    assert r.status_code == expected, r.text


@pytest.mark.parametrize(
    ("role", "expected"),
    [("owner", 200), ("admin", 200), ("member", 403), ("outsider", 404)],
)
async def test_deleting_someone_elses_assistant(
    client: AsyncClient, role: str, expected: int
) -> None:
    _, h, aid = await _actors(client)
    assert (await client.delete(f"/api/v1/assistants/{aid}", headers=h[role])).status_code == (
        expected
    )


@pytest.mark.parametrize(
    ("role", "expected"),
    [("owner", 200), ("admin", 200), ("member", 200), ("outsider", 404)],
)
async def test_listing_connections(client: AsyncClient, role: str, expected: int) -> None:
    _, h, aid = await _actors(client)
    r = await client.get(f"/api/v1/assistants/{aid}/db-connections", headers=h[role])
    assert r.status_code == expected


@pytest.mark.parametrize(
    ("role", "expected"),
    [("owner", 200), ("admin", 200), ("member", 403), ("outsider", 404)],
)
async def test_the_audit_log_is_for_admins(client: AsyncClient, role: str, expected: int) -> None:
    team, h, _ = await _actors(client)
    r = await client.get(f"/api/v1/orgs/{team.org_id}/audit-log", headers=h[role])
    assert r.status_code == expected


@pytest.mark.parametrize(
    ("role", "expected"),
    [("owner", 200), ("admin", 200), ("member", 200), ("outsider", 404)],
)
async def test_usage_is_visible_to_members(client: AsyncClient, role: str, expected: int) -> None:
    team, h, _ = await _actors(client)
    r = await client.get(f"/api/v1/orgs/{team.org_id}/usage", headers=h[role])
    assert r.status_code == expected


@pytest.mark.parametrize(
    ("role", "expected"),
    [("owner", 201), ("admin", 201), ("member", 403), ("outsider", 404)],
)
async def test_inviting_is_for_admins(client: AsyncClient, role: str, expected: int) -> None:
    team, h, _ = await _actors(client)
    r = await client.post(
        f"/api/v1/orgs/{team.org_id}/invites",
        json={"email": f"new-{role}@example.com", "role": "member"},
        headers=h[role],
    )
    assert r.status_code == expected, r.text


@pytest.mark.parametrize(
    ("role", "expected"),
    [("owner", 201), ("admin", 201), ("member", 403), ("outsider", 404)],
)
async def test_registering_an_mcp_server_on_someone_elses_assistant(
    client: AsyncClient, role: str, expected: int
) -> None:
    _, h, aid = await _actors(client)
    r = await client.post(
        f"/api/v1/assistants/{aid}/mcp-servers",
        json={"name": "tools", "transport": "http", "url": "https://mcp.example.com/mcp"},
        headers=h[role],
    )
    assert r.status_code == expected, r.text


@pytest.mark.parametrize(
    ("role", "expected"),
    [("admin", 200), ("member", 403), ("outsider", 404)],
)
async def test_checking_an_mcp_server_on_someone_elses_assistant(
    client: AsyncClient, role: str, expected: int
) -> None:
    """Checking a server connects to it with its stored credentials (and, for
    a local command, starts it), so only editors may."""
    _, h, aid = await _actors(client)
    sid = (
        await client.post(
            f"/api/v1/assistants/{aid}/mcp-servers",
            json={"name": "tools", "transport": "http", "url": "https://mcp.example.com/mcp"},
            headers=h["owner"],
        )
    ).json()["id"]
    r = await client.post(f"/api/v1/assistants/{aid}/mcp-servers/{sid}:health", headers=h[role])
    assert r.status_code == expected, r.text
