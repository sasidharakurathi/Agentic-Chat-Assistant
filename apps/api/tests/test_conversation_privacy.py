"""Conversations are private (Phase 7b.2, plan D5).

Each person lists and reads only their own. An admin or the owner may read
anyone's, giving a reason; the read is recorded, and committed, before any
content is returned, once per admin, conversation and reason in half an
hour. Lists and usage leave other people's titles out (a title is the start
of a first message), and the audit log holds no conversation content.
"""

from __future__ import annotations

import uuid
from typing import Any
from urllib.parse import quote

import pytest
from app.api import deps
from app.db.session import get_sessionmaker
from app.main import app
from app.models.audit_log import AuditLog
from app.models.usage import UsageEvent, UsageKind
from app.services import conversation_reads
from fastapi.routing import APIRoute
from httpx import AsyncClient
from sqlalchemy import select
from tests.versions import go_live

from test_rbac_escalation import Team, _team  # type: ignore[import-not-found]

pytestmark = pytest.mark.anyio

API = "/api/v1"
READS = ("", "/messages", "/runs", "/approvals", "/turn")


def _depends_on(dependant: Any, wanted: set[Any]) -> bool:
    return any(d.call in wanted or _depends_on(d, wanted) for d in dependant.dependencies)


def _api_routes(routes: list[Any]) -> list[APIRoute]:
    """Every endpoint, through the included routers (FastAPI nests them)."""
    out: list[APIRoute] = []
    for r in routes:
        if isinstance(r, APIRoute):
            out.append(r)
        elif hasattr(r, "original_router"):
            out.extend(_api_routes(r.original_router.routes))
    return out


def test_every_conversation_route_checks_who_may_read_or_act() -> None:
    """A route added later that reaches a conversation without the check
    would be a read nobody recorded."""
    # The Chat twins (Phase 8.1) apply the same rules, employees included.
    readers = {deps.require_conversation_reader, deps.require_chat_conversation_reader}
    guards = readers | {deps.require_conversation_starter, deps.require_chat_conversation_starter}
    routes = [r for r in _api_routes(app.routes) if "{conversation_id}" in r.path]
    unguarded = [
        f"{sorted(r.methods)} {r.path}" for r in routes if not _depends_on(r.dependant, guards)
    ]
    assert unguarded == []
    reads = [r for r in routes if "GET" in r.methods]
    assert len(reads) >= 7
    assert all(_depends_on(r.dependant, readers) for r in reads), (
        "a read is a read, whoever's turn it is"
    )


@pytest.fixture
async def team(client: AsyncClient) -> Team:
    return await _team(client)


async def _conversation(client: AsyncClient, team: Team, who: Any, title: str = "") -> str:
    """A live conversation `who` starts on the owner's approved assistant."""
    a = await client.post(f"{API}/assistants", json={"name": "Desk"}, headers=team.h(team.owner))
    aid = a.json()["id"]
    await go_live(client, aid, team.h(team.owner), approver=team.h(team.admin))
    c = await client.post(
        f"{API}/assistants/{aid}/conversations", json={"title": title or None}, headers=team.h(who)
    )
    assert c.status_code == 201, c.text
    return str(c.json()["id"])


def _with_reason(h: dict[str, str], reason: str) -> dict[str, str]:
    return {**h, conversation_reads.HEADER: quote(reason)}


async def _reads() -> list[AuditLog]:
    async with get_sessionmaker()() as s:
        return list(
            await s.scalars(select(AuditLog).where(AuditLog.action == conversation_reads.ACTION))
        )


async def test_a_teammate_can_neither_list_nor_read_your_conversation(
    client: AsyncClient, team: Team
) -> None:
    cid = await _conversation(client, team, team.member, "My salary question")
    aid = (await client.get(f"{API}/conversations/{cid}", headers=team.h(team.member))).json()[
        "assistant_id"
    ]
    other = team.h(team.member2)
    for path in READS:
        r = await client.get(f"{API}/conversations/{cid}{path}", headers=other)
        assert (r.status_code, r.json()["error"]["code"]) == (403, "not_conversation_owner"), path
    listed = await client.get(f"{API}/assistants/{aid}/conversations", headers=other)
    assert listed.json()["items"] == []
    r = await client.get(f"{API}/assistants/{aid}/conversations?everyone=true", headers=other)
    assert r.status_code == 403
    mine = await client.get(f"{API}/assistants/{aid}/conversations", headers=team.h(team.member))
    assert [c["title"] for c in mine.json()["items"]] == ["My salary question"]
    assert await _reads() == [], "your own reads are not recorded"


async def test_an_admin_reads_with_a_reason_recorded_before_the_content(
    client: AsyncClient, team: Team
) -> None:
    cid = await _conversation(client, team, team.member, "My salary question")
    admin = team.h(team.admin)

    r = await client.get(f"{API}/conversations/{cid}", headers=admin)
    assert (r.status_code, r.json()["error"]["code"]) == (403, "read_reason_required")
    assert await _reads() == []

    why = _with_reason(admin, "Ticket 4411: wrong refund amount")
    for path in READS:
        assert (await client.get(f"{API}/conversations/{cid}{path}", headers=why)).is_success
    (row,) = await _reads()
    assert row.actor_user_id == uuid.UUID(team.admin.user_id)
    assert row.target_id == cid
    assert row.meta == {
        "reason": "Ticket 4411: wrong refund amount",
        "reader_email": "admin@example.com",
        "reader_role": "admin",
        "owner_id": team.member.user_id,
        # Phase 8.8: whether its owner had removed it from their history.
        "hidden_by_owner": False,
    }
    assert "salary" not in str(row.meta), "no conversation content in the record"

    # A different reason is a different read.
    other = _with_reason(admin, "Follow-up on ticket 4411")
    assert (await client.get(f"{API}/conversations/{cid}", headers=other)).is_success
    assert len(await _reads()) == 2


async def test_the_read_is_recorded_even_when_the_request_then_fails(
    client: AsyncClient, team: Team
) -> None:
    """Committed in the check, not with the request: the request's own
    commit comes after its response, too late to promise anything."""
    cid = await _conversation(client, team, team.member)
    why = _with_reason(team.h(team.owner), "Audit sample")
    r = await client.get(f"{API}/conversations/{cid}/messages?cursor=garbage", headers=why)
    assert r.status_code == 400
    assert len(await _reads()) == 1


async def test_the_reason_may_be_any_language_but_not_any_length(
    client: AsyncClient, team: Team
) -> None:
    cid = await _conversation(client, team, team.member)
    admin = team.h(team.admin)
    r = await client.get(f"{API}/conversations/{cid}", headers=_with_reason(admin, "जाँच 12"))
    assert r.is_success
    assert (await _reads())[0].meta["reason"] == "जाँच 12"
    r = await client.get(f"{API}/conversations/{cid}", headers=_with_reason(admin, "x" * 501))
    assert (r.status_code, r.json()["error"]["code"]) == (400, "read_reason_too_long")


async def test_an_admins_list_of_everyones_conversations_has_no_titles(
    client: AsyncClient, team: Team
) -> None:
    cid = await _conversation(client, team, team.member, "My salary question")
    aid = (await client.get(f"{API}/conversations/{cid}", headers=team.h(team.member))).json()[
        "assistant_id"
    ]
    admin = team.h(team.admin)
    assert (await client.get(f"{API}/assistants/{aid}/conversations", headers=admin)).json()[
        "items"
    ] == [], "by default, an admin's own"
    (row,) = (
        await client.get(f"{API}/assistants/{aid}/conversations?everyone=true", headers=admin)
    ).json()["items"]
    assert (row["id"], row["title"], row["title_hidden"]) == (cid, "Conversation", True)
    assert row["created_by"] == team.member.user_id


async def test_usage_by_conversation_titles_only_your_own(client: AsyncClient, team: Team) -> None:
    mine = await _conversation(client, team, team.member, "Mine")
    theirs = await _conversation(client, team, team.member2, "Theirs")
    async with get_sessionmaker()() as s:
        for cid in (mine, theirs):
            s.add(
                UsageEvent(
                    org_id=uuid.UUID(team.org_id),
                    conversation_id=uuid.UUID(cid),
                    kind=UsageKind.llm,
                    cost_usd=0.01,
                )
            )
        await s.commit()
    url = f"{API}/orgs/{team.org_id}/usage?group_by=conversation"

    rows = (await client.get(url, headers=team.h(team.member))).json()["rows"]
    assert [(r["group_key"], r["label"]) for r in rows] == [(mine, "Mine")], "only their own"
    rows = (await client.get(url, headers=team.h(team.admin))).json()["rows"]
    assert {r["group_key"]: r["label"] for r in rows} == {mine: None, theirs: None}


async def test_renaming_records_no_title(client: AsyncClient, team: Team) -> None:
    cid = await _conversation(client, team, team.member, "My salary question")
    r = await client.patch(
        f"{API}/conversations/{cid}", json={"title": "Pay"}, headers=team.h(team.member)
    )
    assert r.is_success
    async with get_sessionmaker()() as s:
        meta = await s.scalar(select(AuditLog.meta).where(AuditLog.action == "conversation.rename"))
    assert meta == {"from_length": 18, "to_length": 3}
