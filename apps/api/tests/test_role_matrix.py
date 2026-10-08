"""Who may call what inside one org, route by route (Phase 7a.3).

`test_tenant_isolation.py` walks every route as someone from another org.
This is its in-org twin: one org with an owner, an admin, a member who
built the assistant (the creator) and a member who did not. `ROLE_MATRIX`
says, for every route the API publishes, which of them may call it; the
walk asks each route as each of them and checks the answer.

Three promises:

- **The table is complete.** A route added without an entry here fails,
  so nobody has to remember to decide who may call it.
- **The table is true.** Each refusal it promises is a 403. Each access it
  promises gets past the checks: a read answers, and a write with a body
  is turned away only for the body (422), which proves it got past the
  access checks without changing anything. Writes with no body are not
  sent as an allowed role (they would delete the walk's own objects); the
  other test files cover them.
- **Deny by default.** With the Studio floor raised above `member`, a
  member stands in for a role that does not exist yet (the Employee role,
  Phase 8). Every route outside `PUBLIC` and `SIGNED_IN` must then refuse
  them with `studio_access_required`. A new route that loads an org's data
  without the floor fails here.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from app import queue
from app.db.session import get_sessionmaker
from app.evals import runner
from app.main import app
from app.models.enums import MemberRole
from app.models.integration import McpServer, McpTransport
from app.security import access
from app.services import approvals as approvals_svc
from httpx import AsyncClient

from test_orgs import Actor, _register  # type: ignore[import-not-found]

pytestmark = pytest.mark.anyio

API = "/api/v1"
METHODS = ("get", "post", "put", "patch", "delete")

# ── the matrix ───────────────────────────────────────────────

#: No sign-in at all.
PUBLIC = "public"
#: Signed in; about the caller, not about any org. A role below the floor
#: still reaches these. `POST /orgs` is one: a person can always start their
#: own org (personal orgs go away with Google sign-in, Phase 8).
SIGNED_IN = "signed-in"
#: Any member of the org at or above the Studio floor.
MEMBER = "member"
#: The assistant's creator, or an admin or owner.
EDITOR = "editor"
#: The person who started the conversation, and nobody else: admins read
#: other people's conversations but cannot act in them (Phase 7a.4).
STARTER = "starter"
#: The person who started the conversation, or an admin or owner: deciding
#: an approval it is waiting on (today's in-chat approval, plan D17).
DECIDER = "decider"
#: Admins and owners.
ADMIN = "admin"

ROLE_MATRIX: dict[str, str] = {
    # Health and sign-in.
    "GET /healthz": PUBLIC,
    "GET /readyz": PUBLIC,
    "POST /api/v1/auth/register": PUBLIC,
    "POST /api/v1/auth/login": PUBLIC,
    "POST /api/v1/auth/refresh": PUBLIC,
    "POST /api/v1/auth/logout": PUBLIC,
    "GET /api/v1/invites/{token}": PUBLIC,
    "GET /api/v1/meta/config-schema": PUBLIC,
    "GET /api/v1/meta/graph-schema": PUBLIC,
    "GET /api/v1/meta/samples": PUBLIC,
    "GET /api/v1/auth/me": SIGNED_IN,
    "GET /api/v1/orgs": SIGNED_IN,
    "POST /api/v1/orgs": SIGNED_IN,
    "POST /api/v1/invites/{token}/accept": SIGNED_IN,
    # The org.
    "GET /api/v1/orgs/{org_id}/members": MEMBER,
    "PATCH /api/v1/orgs/{org_id}/members/{user_id}": ADMIN,
    "POST /api/v1/orgs/{org_id}/invites": ADMIN,
    "GET /api/v1/orgs/{org_id}/usage": MEMBER,
    "GET /api/v1/orgs/{org_id}/audit-log": ADMIN,
    "GET /api/v1/orgs/{org_id}/budgets": MEMBER,
    "PUT /api/v1/orgs/{org_id}/budgets": ADMIN,
    # Assistants and their versions.
    "GET /api/v1/assistants": MEMBER,
    "POST /api/v1/assistants": MEMBER,
    "POST /api/v1/assistants:from-sample": MEMBER,
    "POST /api/v1/pipeline:recommend": MEMBER,
    "GET /api/v1/assistants/{assistant_id}": MEMBER,
    "PATCH /api/v1/assistants/{assistant_id}": EDITOR,
    "DELETE /api/v1/assistants/{assistant_id}": EDITOR,
    "GET /api/v1/assistants/{assistant_id}/draft-graph": MEMBER,
    "PUT /api/v1/assistants/{assistant_id}/draft-graph": EDITOR,
    "PUT /api/v1/assistants/{assistant_id}/draft-config": EDITOR,
    "POST /api/v1/assistants/{assistant_id}/graph:validate": MEMBER,
    "POST /api/v1/assistants/{assistant_id}/graph:compile": MEMBER,
    "POST /api/v1/assistants/{assistant_id}/versions": EDITOR,
    "GET /api/v1/assistants/{assistant_id}/versions": MEMBER,
    "GET /api/v1/assistants/{assistant_id}/versions/diff": MEMBER,
    "GET /api/v1/assistants/{assistant_id}/versions/{number}": MEMBER,
    "POST /api/v1/assistants/{assistant_id}/prompt:generate": EDITOR,
    "POST /api/v1/assistants/{assistant_id}/pipeline:recommend": EDITOR,
    "GET /api/v1/assistants/{assistant_id}/budget": MEMBER,
    "PUT /api/v1/assistants/{assistant_id}/budget": ADMIN,
    "GET /api/v1/assistants/{assistant_id}/memories": MEMBER,
    "DELETE /api/v1/assistants/{assistant_id}/memories": MEMBER,
    # Conversations.
    "POST /api/v1/assistants/{assistant_id}/conversations": MEMBER,
    "GET /api/v1/assistants/{assistant_id}/conversations": MEMBER,
    "GET /api/v1/conversations/{conversation_id}": MEMBER,
    "PATCH /api/v1/conversations/{conversation_id}": STARTER,
    "DELETE /api/v1/conversations/{conversation_id}": STARTER,
    "POST /api/v1/conversations/{conversation_id}:interrupt": STARTER,
    "GET /api/v1/conversations/{conversation_id}/messages": MEMBER,
    "POST /api/v1/conversations/{conversation_id}/messages": STARTER,
    "GET /api/v1/conversations/{conversation_id}/runs": MEMBER,
    "GET /api/v1/conversations/{conversation_id}/runs/{run_id}": MEMBER,
    "GET /api/v1/conversations/{conversation_id}/turn": MEMBER,
    "GET /api/v1/conversations/{conversation_id}/turns/{turn_id}/events": MEMBER,
    "GET /api/v1/conversations/{conversation_id}/approvals": MEMBER,
    "POST /api/v1/approvals/{approval_id}:resolve": DECIDER,
    # Knowledge.
    "GET /api/v1/assistants/{assistant_id}/data-sources": MEMBER,
    "POST /api/v1/assistants/{assistant_id}/data-sources": EDITOR,
    "POST /api/v1/assistants/{assistant_id}/data-sources/upload": EDITOR,
    "GET /api/v1/assistants/{assistant_id}/data-sources/{data_source_id}": MEMBER,
    "GET /api/v1/assistants/{assistant_id}/data-sources/{data_source_id}/content-url": MEMBER,
    "POST /api/v1/assistants/{assistant_id}/data-sources/{data_source_id}:reindex": EDITOR,
    "DELETE /api/v1/assistants/{assistant_id}/data-sources/{data_source_id}": EDITOR,
    # Databases.
    "GET /api/v1/assistants/{assistant_id}/db-connections": MEMBER,
    "POST /api/v1/assistants/{assistant_id}/db-connections": EDITOR,
    "GET /api/v1/assistants/{assistant_id}/db-connections/{connection_id}": MEMBER,
    "PATCH /api/v1/assistants/{assistant_id}/db-connections/{connection_id}": EDITOR,
    "DELETE /api/v1/assistants/{assistant_id}/db-connections/{connection_id}": EDITOR,
    "POST /api/v1/assistants/{assistant_id}/db-connections/{connection_id}:test": EDITOR,
    "POST /api/v1/assistants/{assistant_id}/db-connections/{connection_id}:refresh-schema": EDITOR,
    "GET /api/v1/assistants/{assistant_id}/db-connections/{connection_id}/schema": MEMBER,
    # MCP servers.
    "GET /api/v1/assistants/{assistant_id}/mcp-servers": MEMBER,
    "POST /api/v1/assistants/{assistant_id}/mcp-servers": EDITOR,
    "GET /api/v1/assistants/{assistant_id}/mcp-servers/{server_id}": MEMBER,
    "PATCH /api/v1/assistants/{assistant_id}/mcp-servers/{server_id}": EDITOR,
    "DELETE /api/v1/assistants/{assistant_id}/mcp-servers/{server_id}": EDITOR,
    "POST /api/v1/assistants/{assistant_id}/mcp-servers/{server_id}:health": EDITOR,
    "POST /api/v1/assistants/{assistant_id}/mcp-servers/{server_id}:discover-tools": EDITOR,
    "GET /api/v1/mcp-presets": MEMBER,
    "GET /api/v1/mcp-runner": MEMBER,
    # Evals.
    "GET /api/v1/assistants/{assistant_id}/eval-suites": MEMBER,
    "POST /api/v1/assistants/{assistant_id}/eval-suites": EDITOR,
    "GET /api/v1/eval-suites/{suite_id}": MEMBER,
    "PATCH /api/v1/eval-suites/{suite_id}": EDITOR,
    "DELETE /api/v1/eval-suites/{suite_id}": EDITOR,
    "POST /api/v1/eval-suites/{suite_id}/cases:bulk": EDITOR,
    "PUT /api/v1/eval-suites/{suite_id}/cases/{case_id}": EDITOR,
    "DELETE /api/v1/eval-suites/{suite_id}/cases/{case_id}": EDITOR,
    "POST /api/v1/eval-suites/{suite_id}/runs": EDITOR,
    "GET /api/v1/eval-suites/{suite_id}/runs": MEMBER,
    "GET /api/v1/eval-runs/{run_id}": MEMBER,
    "POST /api/v1/eval-runs/{run_id}:cancel": EDITOR,
}

#: Who passes each level, among the walk's four people.
PASSES: dict[str, set[str]] = {
    MEMBER: {"owner", "admin", "creator", "member"},
    EDITOR: {"owner", "admin", "creator"},
    STARTER: {"creator"},
    DECIDER: {"owner", "admin", "creator"},
    ADMIN: {"owner", "admin"},
}

#: Routes that check the caller's role in the handler, after the body is
#: read: a refusal needs a valid body to be reached.
BODIES: dict[str, Any] = {
    "POST /api/v1/approvals/{approval_id}:resolve": {"decision": "approved"},
    "PUT /api/v1/assistants/{assistant_id}/budget": {"daily_usd": 5},
}


def _routes() -> list[tuple[str, str, bool]]:
    """(key, path, takes a body) for every route the API publishes."""
    out = []
    for path, operations in sorted(app.openapi()["paths"].items()):
        for method in METHODS:
            if method in operations:
                out.append((f"{method.upper()} {path}", path, "requestBody" in operations[method]))
    return out


def test_every_route_is_in_the_matrix_and_nothing_else_is() -> None:
    published = {key for key, _, _ in _routes()}
    missing = sorted(published - ROLE_MATRIX.keys())
    stale = sorted(ROLE_MATRIX.keys() - published)
    assert missing == [], f"decide who may call these: {missing}"
    assert stale == [], f"no longer published: {stale}"
    assert len(published) > 80, "the walk should cover the whole API"


def test_every_role_that_exists_today_reaches_the_studio() -> None:
    """The floor locks out nobody yet: it is there for roles added later."""
    assert access.STUDIO_FLOOR is MemberRole.member
    assert all(role.satisfies(access.STUDIO_FLOOR) for role in MemberRole)


# ── the org the walk asks about ──────────────────────────────


@dataclass
class Team:
    people: dict[str, Actor]
    org_id: str
    ids: dict[str, str]
    eval_run_id: str

    def h(self, who: str) -> dict[str, str]:
        return {**self.people[who].headers, "X-Org-Id": self.org_id}


async def _team(client: AsyncClient, tmp: Path) -> Team:
    owner = await _register(client, "owner@example.com")
    org = (await client.post(f"{API}/orgs", json={"name": "Team"}, headers=owner.headers)).json()
    people = {"owner": owner}
    for name, role in (("admin", "admin"), ("creator", "member"), ("member", "member")):
        actor = await _register(client, f"{name}@example.com")
        inv = await client.post(
            f"{API}/orgs/{org['id']}/invites",
            json={"email": f"{name}@example.com", "role": role},
            headers=owner.headers,
        )
        assert inv.status_code == 201, inv.text
        token = inv.json()["accept_url"].rsplit("/", 1)[-1]
        accepted = await client.post(f"{API}/invites/{token}/accept", headers=actor.headers)
        assert accepted.is_success, accepted.text
        people[name] = actor

    h = {**people["creator"].headers, "X-Org-Id": org["id"]}

    async def made(method: str, url: str, **kw: Any) -> dict[str, Any]:
        r = await client.request(method, f"{API}{url}", headers=h, **kw)
        assert r.status_code in (200, 201, 202), f"{method} {url}: {r.status_code} {r.text}"
        return dict(r.json())

    # Everything belongs to the creator, a plain member.
    aid = (await made("POST", "/assistants", json={"name": "Helpdesk"}))["id"]
    await made("POST", f"/assistants/{aid}/versions", json={"note": "v1"})
    conv = await made("POST", f"/assistants/{aid}/conversations", json={})
    async with client.stream(
        "POST", f"{API}/conversations/{conv['id']}/messages", json={"text": "hello"}, headers=h
    ) as stream:
        turn_id = stream.headers["x-turn-id"]
        async for _ in stream.aiter_lines():
            pass
    run = (await made("GET", f"/conversations/{conv['id']}/runs"))["items"][0]
    source = await made(
        "POST",
        f"/assistants/{aid}/data-sources",
        json={"type": "text", "name": "Notes", "text": "Password resets take a day."},
    )
    db = tmp / f"{uuid.uuid4().hex}.db"
    sqlite3.connect(db).close()
    conn = await made(
        "POST",
        f"/assistants/{aid}/db-connections",
        json={"name": "Tickets", "engine": "sqlite", "database": str(db)},
    )
    suite = await made("POST", f"/assistants/{aid}/eval-suites", json={"name": "Basics"})
    detail = await made(
        "POST", f"/eval-suites/{suite['id']}/cases:bulk", json={"cases": [{"input": "hello"}]}
    )
    eval_run = await made("POST", f"/eval-suites/{suite['id']}/runs", json={})
    await runner.run(uuid.UUID(eval_run["id"]))

    async with get_sessionmaker()() as s:
        server = McpServer(
            assistant_id=uuid.UUID(aid),
            org_id=uuid.UUID(org["id"]),
            name="files",
            transport=McpTransport.http,
            url="https://mcp.example.com/mcp",
        )
        s.add(server)
        await s.commit()
        approval = await approvals_svc.create(
            s,
            conversation_id=uuid.UUID(conv["id"]),
            org_id=uuid.UUID(org["id"]),
            tool_name="mcp__caps__sql_query",
            tool_input={"sql": "UPDATE tickets SET status = 'closed'"},
            risk="high",
            rationale="writes",
        )
        server_id, approval_id = str(server.id), str(approval.id)

    return Team(
        people=people,
        org_id=org["id"],
        eval_run_id=eval_run["id"],
        ids={
            "org_id": org["id"],
            "assistant_id": aid,
            "number": "1",
            "conversation_id": conv["id"],
            "run_id": run["id"],
            "turn_id": turn_id,
            "data_source_id": source["id"],
            "connection_id": conn["id"],
            "server_id": server_id,
            "approval_id": approval_id,
            "suite_id": suite["id"],
            "case_id": detail["cases"][0]["id"],
            # A role change aimed at the plain member.
            "user_id": people["member"].user_id,
        },
    )


def _fill(path: str, team: Team) -> str:
    url = path
    for name, value in team.ids.items():
        filled = team.eval_run_id if name == "run_id" and "/eval-runs/" in path else value
        url = url.replace("{" + name + "}", filled)
    assert "{" not in url, f"no id for {url}: add it to _team"
    return url


@pytest.fixture
async def team(client: AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Team:
    async def enqueue(_id: uuid.UUID) -> bool:
        return True

    monkeypatch.setattr(queue, "enqueue_eval", enqueue)
    monkeypatch.setattr(queue, "enqueue_ingest", enqueue)
    return await _team(client, tmp_path)


def _org_routes() -> list[tuple[str, str, bool]]:
    return [r for r in _routes() if ROLE_MATRIX[r[0]] not in (PUBLIC, SIGNED_IN)]


# ── the walk ─────────────────────────────────────────────────


async def test_each_role_is_refused_exactly_where_the_matrix_says(
    client: AsyncClient, team: Team
) -> None:
    wrong: list[str] = []
    refused = 0
    for key, path, _ in _org_routes():
        method = key.split(" ", 1)[0]
        for who in ("member", "creator", "admin", "owner"):
            if who in PASSES[ROLE_MATRIX[key]]:
                continue
            r = await client.request(
                method, _fill(path, team), headers=team.h(who), json=BODIES.get(key, {})
            )
            refused += 1
            if r.status_code != 403:
                wrong.append(f"{who}: {key} -> {r.status_code} {r.text[:120]}")
    assert wrong == [], "\n".join(wrong)
    assert refused > 40


async def test_each_role_gets_through_where_the_matrix_says(
    client: AsyncClient, team: Team
) -> None:
    """Reads are sent as they are; writes with a body are sent a body of the
    wrong shape, so passing the checks shows up as 422 and changes nothing."""
    wrong: list[str] = []
    tried = 0
    for key, path, has_body in _org_routes():
        method = key.split(" ", 1)[0]
        if method != "GET" and not has_body:
            continue
        for who in sorted(PASSES[ROLE_MATRIX[key]]):
            kw: dict[str, Any] = {} if method == "GET" else {"json": []}
            r = await client.request(method, _fill(path, team), headers=team.h(who), **kw)
            tried += 1
            if r.status_code in (401, 403, 404) or (method != "GET" and r.status_code != 422):
                wrong.append(f"{who}: {key} -> {r.status_code} {r.text[:120]}")
    assert wrong == [], "\n".join(wrong)
    assert tried > 150


async def test_a_role_below_the_floor_reaches_no_org_route(
    client: AsyncClient, team: Team, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deny by default. Raising the floor to admin makes a plain member
    stand in for a role below it, such as the Employee role to come."""
    monkeypatch.setattr(access, "STUDIO_FLOOR", MemberRole.admin)
    wrong: list[str] = []
    for key, path, _ in _org_routes():
        method = key.split(" ", 1)[0]
        r = await client.request(
            method, _fill(path, team), headers=team.h("member"), json=BODIES.get(key, {})
        )
        code = r.json().get("error", {}).get("code") if r.status_code == 403 else None
        if code != "studio_access_required":
            wrong.append(f"{key} -> {r.status_code} {r.text[:120]}")
    assert wrong == [], "\n".join(wrong)

    # Their own profile and org list still answer: they are signed in.
    for url in ("/auth/me", "/orgs"):
        assert (await client.get(f"{API}{url}", headers=team.h("member"))).status_code == 200


async def test_nothing_changed_after_the_walk(client: AsyncClient, team: Team) -> None:
    """Every refusal above came before anything was written."""
    for key, path, _ in _org_routes():
        method = key.split(" ", 1)[0]
        if method == "GET":
            continue
        for who in ("member", "creator", "admin"):
            if who not in PASSES[ROLE_MATRIX[key]]:
                await client.request(
                    method, _fill(path, team), headers=team.h(who), json=BODIES.get(key, {})
                )
    ids, h = team.ids, team.h("owner")
    for url in (
        f"/assistants/{ids['assistant_id']}",
        f"/conversations/{ids['conversation_id']}",
        f"/assistants/{ids['assistant_id']}/data-sources/{ids['data_source_id']}",
        f"/assistants/{ids['assistant_id']}/db-connections/{ids['connection_id']}",
        f"/assistants/{ids['assistant_id']}/mcp-servers/{ids['server_id']}",
        f"/eval-suites/{ids['suite_id']}",
    ):
        assert (await client.get(f"{API}{url}", headers=h)).status_code == 200, url
    pending = await client.get(f"{API}/conversations/{ids['conversation_id']}/approvals", headers=h)
    assert [a["id"] for a in pending.json()["items"]] == [ids["approval_id"]], "still undecided"
    roles = (await client.get(f"{API}/orgs/{team.org_id}/members", headers=h)).json()["items"]
    assert {r["user_id"]: r["role"] for r in roles}[ids["user_id"]] == "member"
    budget = (await client.get(f"{API}/assistants/{ids['assistant_id']}/budget", headers=h)).json()
    assert budget["budgets"] == [], "no refused caller set a budget"
