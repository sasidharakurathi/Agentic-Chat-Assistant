"""Tenant isolation, route by route (task 6.3).

`test_tenancy.py` checks the session-level org filter and a handful of
routes by hand. This walks **every** route the API publishes. One org owns
one of everything; someone from another org then asks for each route with
the first org's ids, and must be told "not found" every time: not the
object, not "forbidden" (which would confirm it exists), not a validation
error about the body (which would mean the body was looked at first).

Two attacks per route:

- **the outsider**: every id in the path is the victim's;
- **the borrowed parent**: the attacker's own assistant (or conversation,
  suite, org) in the path, with the victim's child id under it. This is
  the classic hole: the parent passes the membership check, and the child
  is then loaded by id without asking whose it is.

The walk is driven by the OpenAPI schema, so a route added later is
covered without anyone remembering to. A path parameter this file doesn't
know how to fill fails the test.
"""

from __future__ import annotations

import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from app import queue
from app.db.session import get_sessionmaker
from app.evals import runner
from app.main import app
from app.models.approval import ApprovalDelegation
from app.models.attachment import Attachment
from app.models.conversation import ConversationShare
from app.models.integration import HttpConnection, McpServer, McpTransport
from httpx import AsyncClient
from tests.pending import waiting_request
from tests.versions import go_live

from test_orgs import Actor, _register  # type: ignore[import-not-found]

pytestmark = pytest.mark.anyio

API = "/api/v1"
METHODS = ("get", "post", "put", "patch", "delete")
#: A path whose only parameter is itself the secret (an invite link), or
#: that names nothing owned by an org.
NOT_OWNED = {"token"}
#: Routes that read their body before they look anything up. An empty body
#: gets a validation error whoever asks, which says nothing about whether
#: the object exists; with a valid one they must still say "not found".
BODIES: dict[tuple[str, str], dict[str, Any]] = {
    ("POST", "/api/v1/approvals/{approval_id}:resolve"): {"decision": "approved"},
    ("PUT", "/api/v1/assistants/{assistant_id}/data-sources/{data_source_id}/access"): {
        "everyone": True
    },
    ("POST", "/api/v1/assistants/{assistant_id}/review-queue/{item_id}:dismiss"): {
        "reason": "not mine"
    },
    ("POST", "/api/v1/assistants/{assistant_id}/review-queue/{item_id}:to-case"): {
        "suite_id": "00000000-0000-0000-0000-000000000000",
        "expected": {},
    },
    ("PUT", "/api/v1/eval-suites/{suite_id}/cases/{case_id}"): {"input": "rewritten"},
    ("PATCH", "/api/v1/orgs/{org_id}/members/{user_id}"): {"role": "admin"},
    # Phase 8.5 to 8.10: well-formed, so what is tested is whose they are.
    ("POST", "/api/v1/orgs/{org_id}/groups/{group_id}/members"): {
        "user_id": "00000000-0000-0000-0000-000000000000"
    },
    ("PUT", "/api/v1/conversations/{conversation_id}/messages/{message_id}/feedback"): {
        "rating": "up"
    },
    ("POST", "/api/v1/orgs/{org_id}/members/{user_id}:erase"): {
        "confirm_email": "someone@example.com"
    },
}


@dataclass
class World:
    """One org's worth of objects, and the ids that name them in a path."""

    actor: Actor
    headers: dict[str, str]
    ids: dict[str, str]
    #: `run_id` means a chat run under /conversations and an eval run
    #: under /eval-runs.
    eval_run_id: str


async def _world(client: AsyncClient, email: str, tmp: Path) -> World:
    actor = await _register(client, email)
    me = (await client.get(f"{API}/auth/me", headers=actor.headers)).json()
    org = me["memberships"][0]["org_id"]
    h = {**actor.headers, "X-Org-Id": org}

    async def made(method: str, url: str, **kw: Any) -> dict[str, Any]:
        r = await client.request(method, f"{API}{url}", headers=h, **kw)
        assert r.status_code in (200, 201, 202), f"{method} {url}: {r.status_code} {r.text}"
        return dict(r.json())

    a = await made("POST", "/assistants", json={"name": f"Owned by {email}"})
    aid = a["id"]
    await go_live(client, aid, h, "v1")

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
        json={"type": "text", "name": "Notes", "text": "Refunds take 30 days."},
    )

    shop = tmp / f"{uuid.uuid4().hex}.db"
    sqlite3.connect(shop).close()
    conn = await made(
        "POST",
        f"/assistants/{aid}/db-connections",
        json={"name": "Shop", "engine": "sqlite", "database": str(shop)},
    )

    # An invite still open (Phase 7b.4: it can be withdrawn by id).
    invite = await made(
        "POST",
        f"/orgs/{org}/invites",
        json={"email": f"guest-{aid[:8]}@example.com", "role": "member"},
    )

    group = await made("POST", f"/orgs/{org}/groups", json={"name": f"G-{aid[:8]}"})

    suite = await made("POST", f"/assistants/{aid}/eval-suites", json={"name": "Basics"})
    detail = await made(
        "POST", f"/eval-suites/{suite['id']}/cases:bulk", json={"cases": [{"input": "hello"}]}
    )
    eval_run = await made("POST", f"/eval-suites/{suite['id']}/runs", json={})
    await runner.run(uuid.UUID(eval_run["id"]))

    async with get_sessionmaker()() as s:
        server = McpServer(
            assistant_id=uuid.UUID(aid),
            org_id=uuid.UUID(org),
            name="files",
            transport=McpTransport.http,
            url="https://mcp.example.com/mcp",
        )
        s.add(server)
        http_conn = HttpConnection(
            assistant_id=uuid.UUID(aid),
            org_id=uuid.UUID(org),
            name="Tickets",
            base_url="https://tickets.example.com/api",
            header_names=[],
        )
        s.add(http_conn)
        share = ConversationShare(
            org_id=uuid.UUID(org),
            conversation_id=uuid.UUID(conv["id"]),
            created_by=uuid.UUID(actor.user_id),
            token_hash=uuid.uuid4().hex,
        )
        s.add(share)
        attachment = Attachment(
            org_id=uuid.UUID(org),
            conversation_id=uuid.UUID(conv["id"]),
            uploaded_by=uuid.UUID(actor.user_id),
            object_key="attachments/test.png",
            content_type="image/png",
            bytes=8,
        )
        s.add(attachment)
        await s.commit()
        http_connection_id = str(http_conn.id)
        share_id = str(share.id)
        attachment_id = str(attachment.id)
        approval = await waiting_request(
            s,
            conversation_id=uuid.UUID(conv["id"]),
            tool_name="mcp__caps__sql_query",
            tool_input={"sql": "DELETE FROM orders"},
            risk="high",
            rationale="writes",
        )
        server_id, approval_id = str(server.id), str(approval.id)
        # A hand-over of the victim's own (Phase 9.1), for its route.
        handed = ApprovalDelegation(
            org_id=uuid.UUID(org),
            user_id=uuid.UUID(actor.user_id),
            delegate_id=uuid.UUID(actor.user_id),
            until=datetime.now(UTC) + timedelta(days=1),
        )
        s.add(handed)
        await s.commit()
        delegation_id = str(handed.id)

    return World(
        actor=actor,
        headers=h,
        eval_run_id=eval_run["id"],
        ids={
            "org_id": org,
            "assistant_id": aid,
            "number": "1",
            "conversation_id": conv["id"],
            "run_id": run["id"],
            "message_id": run["message_id"],
            "data_source_id": source["id"],
            "connection_id": conn["id"],
            "server_id": server_id,
            "http_connection_id": http_connection_id,
            "share_id": share_id,
            "attachment_id": attachment_id,
            "item_id": run["message_id"],
            "approval_id": approval_id,
            "invite_id": invite["id"],
            "group_id": group["id"],
            "suite_id": suite["id"],
            "case_id": detail["cases"][0]["id"],
            "user_id": actor.user_id,
            # A turn of the victim's conversation, still readable (QOS-01).
            "turn_id": turn_id,
            "delegation_id": delegation_id,
        },
    )


def _routes() -> list[tuple[str, str, list[str]]]:
    """(METHOD, path template, its parameters) for every published route
    that names something an org owns."""
    out = []
    for path, operations in sorted(app.openapi()["paths"].items()):
        params = re.findall(r"{(\w+)}", path)
        if not params or set(params) <= NOT_OWNED:
            continue
        for method in METHODS:
            if method in operations:
                out.append((method.upper(), path, params))
    return out


def _fill(path: str, params: list[str], ids: dict[str, str], eval_run_id: str) -> str:
    url = path
    for name in params:
        value = eval_run_id if name == "run_id" and "/eval-runs/" in path else ids[name]
        url = url.replace("{" + name + "}", value)
    return url


def test_every_path_parameter_is_one_this_walk_knows() -> None:
    """A new kind of id in a route must be added to `_world`, or the walk
    would skip the routes that use it."""
    known = {
        "org_id",
        "assistant_id",
        "number",
        "conversation_id",
        "run_id",
        "message_id",
        "data_source_id",
        "connection_id",
        "server_id",
        "http_connection_id",
        "share_id",
        "attachment_id",
        "item_id",
        "approval_id",
        "invite_id",
        "group_id",
        "suite_id",
        "case_id",
        "user_id",
        "turn_id",
        "delegation_id",
    } | NOT_OWNED
    seen = {p for _, _, params in _routes() for p in params}
    assert seen <= known, f"new path parameters: {sorted(seen - known)}"
    assert len(_routes()) > 60, "the walk should cover the whole API"


@pytest.fixture
async def worlds(
    client: AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[World, World]:
    async def enqueue(_id: uuid.UUID) -> bool:
        return True

    monkeypatch.setattr(queue, "enqueue_eval", enqueue)
    monkeypatch.setattr(queue, "enqueue_ingest", enqueue)

    async def stored(_key: str) -> bytes:
        return b"\x89PNG\r\n\x1a\n"

    # A screenshot's bytes, without object storage (Phase 9.6).
    monkeypatch.setattr("app.services.attachments.get_object", stored)
    victim = await _world(client, "victim@example.com", tmp_path)
    attacker = await _world(client, "attacker@example.com", tmp_path)
    return victim, attacker


async def test_an_outsider_is_told_not_found_on_every_route(
    client: AsyncClient, worlds: tuple[World, World]
) -> None:
    victim, attacker = worlds
    wrong: list[str] = []
    for method, path, params in _routes():
        url = _fill(path, params, victim.ids, victim.eval_run_id)
        # With the attacker's own org header, and with none: a header for
        # the wrong org must not change the answer.
        for headers in (attacker.headers, attacker.actor.headers):
            body = BODIES.get((method, path), {})
            r = await client.request(method, url, headers=headers, json=body)
            if r.status_code != 404:
                wrong.append(f"{method} {path} -> {r.status_code} {r.text[:120]}")
    assert wrong == [], "\n".join(wrong)


#: The parameters that establish whose request it is; the rest hang off one.
PARENTS = ("org_id", "assistant_id", "conversation_id", "suite_id")


async def test_a_borrowed_parent_does_not_open_someone_elses_child(
    client: AsyncClient, worlds: tuple[World, World]
) -> None:
    victim, attacker = worlds
    wrong: list[str] = []
    tried = 0
    for method, path, params in _routes():
        if len(params) < 2 or params[0] not in PARENTS or params[1] == "number":
            continue
        mixed = {**victim.ids, params[0]: attacker.ids[params[0]]}
        url = _fill(path, params, mixed, victim.eval_run_id)
        body = BODIES.get((method, path), {})
        r = await client.request(method, url, headers=attacker.headers, json=body)
        tried += 1
        if r.status_code != 404:
            wrong.append(f"{method} {path} -> {r.status_code} {r.text[:120]}")
    assert wrong == [], "\n".join(wrong)
    assert tried >= 15


async def test_the_victim_can_reach_everything_the_walk_asks_for(
    client: AsyncClient, worlds: tuple[World, World]
) -> None:
    """The control: the same GETs, as the owner, all work. Without this,
    the walk above could pass because its ids were simply wrong."""
    victim, _ = worlds
    wrong: list[str] = []
    for method, path, params in _routes():
        # Reads that need a query string, or reach outside, aren't the point.
        if method != "GET" or path.endswith(("/diff", "/content-url", "/schema")):
            continue
        url = _fill(path, params, victim.ids, victim.eval_run_id)
        r = await client.get(url, headers=victim.headers)
        if r.status_code != 200:
            wrong.append(f"GET {path} -> {r.status_code} {r.text[:120]}")
    assert wrong == [], "\n".join(wrong)


async def test_nothing_of_the_victims_changed(
    client: AsyncClient, worlds: tuple[World, World]
) -> None:
    """After every write and delete the attacker tried, it is all still
    there."""
    victim, attacker = worlds
    for method, path, params in _routes():
        if method == "GET":
            continue
        url = _fill(path, params, victim.ids, victim.eval_run_id)
        body = BODIES.get((method, path), {})
        await client.request(method, url, headers=attacker.headers, json=body)
    v = victim.ids
    h = victim.headers
    for url in (
        f"/assistants/{v['assistant_id']}",
        f"/conversations/{v['conversation_id']}",
        f"/assistants/{v['assistant_id']}/data-sources/{v['data_source_id']}",
        f"/assistants/{v['assistant_id']}/db-connections/{v['connection_id']}",
        f"/assistants/{v['assistant_id']}/mcp-servers/{v['server_id']}",
        f"/eval-suites/{v['suite_id']}",
        f"/eval-runs/{victim.eval_run_id}",
    ):
        r = await client.get(f"{API}{url}", headers=h)
        assert r.status_code == 200, f"{url}: {r.status_code}"
    assert (await client.get(f"{API}/assistants/{v['assistant_id']}", headers=h)).json()[
        "name"
    ] == "Owned by victim@example.com"
    http = await client.get(f"{API}/assistants/{v['assistant_id']}/http-connections", headers=h)
    assert [(c["id"], c["base_url"]) for c in http.json()] == [
        (v["http_connection_id"], "https://tickets.example.com/api")
    ], "still there, unchanged"
    links = await client.get(f"{API}/conversations/{v['conversation_id']}/shares", headers=h)
    assert [(s["id"], s["revoked_at"]) for s in links.json()] == [(v["share_id"], None)]
    pending = await client.get(f"{API}/conversations/{v['conversation_id']}/approvals", headers=h)
    assert [a["id"] for a in pending.json()["items"]] == [v["approval_id"]], "still undecided"
    invites = await client.get(f"{API}/orgs/{v['org_id']}/invites", headers=h)
    assert v["invite_id"] in [i["id"] for i in invites.json()], "still open"
