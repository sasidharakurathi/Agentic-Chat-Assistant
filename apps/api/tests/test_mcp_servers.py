"""Registering MCP servers (task 4.3).

Same promise as database connections: a credential goes in and never comes
back out. Headers (http/sse) and environment variables (stdio) are sealed,
only their names are returned, and the plain-text fields (command,
arguments, URL) refuse values that look like credentials.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from app.db.session import get_sessionmaker
from app.models.integration import McpServer, McpServerStatus
from app.models.secret import Secret, SecretKind
from app.services import mcp_servers as svc
from httpx import AsyncClient
from sqlalchemy import func, select

pytestmark = pytest.mark.anyio

TOKEN = "ghp_" + "a" * 36  # shaped like a GitHub token
HEADER_VALUE = f"Bearer {TOKEN}"


async def _assistant(client: AsyncClient, headers: dict[str, str]) -> str:
    r = await client.post("/api/v1/assistants", json={"name": "MCP"}, headers=headers)
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def _create(client: AsyncClient, auth: dict[str, str], aid: str, **body: Any):
    return await client.post(f"/api/v1/assistants/{aid}/mcp-servers", json=body, headers=auth)


def _stdio(**over: Any) -> dict[str, Any]:
    return {
        "name": "files",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-everything"],
        "env": {"GITHUB_TOKEN": TOKEN},
    } | over


def _http(**over: Any) -> dict[str, Any]:
    return {
        "name": "tickets",
        "transport": "http",
        "url": "https://mcp.example.com/mcp",
        "headers": {"Authorization": HEADER_VALUE},
    } | over


async def _secret_count() -> int:
    async with get_sessionmaker()() as s:
        return int(await s.scalar(select(func.count()).select_from(Secret)) or 0)


# ── secrets ──────────────────────────────────────────────────


async def test_env_values_are_sealed_and_never_returned(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    aid = await _assistant(client, org_headers)
    r = await _create(client, org_headers, aid, **_stdio())
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["env_keys"] == ["GITHUB_TOKEN"]
    assert body["status"] == "unknown" and body["tools"] == []
    listed = await client.get(f"/api/v1/assistants/{aid}/mcp-servers", headers=org_headers)
    got = await client.get(
        f"/api/v1/assistants/{aid}/mcp-servers/{body['id']}", headers=org_headers
    )
    for text in (r.text, listed.text, got.text):
        assert TOKEN not in text

    async with get_sessionmaker()() as s:
        server = await s.get(McpServer, uuid.UUID(body["id"]))
        assert server is not None and server.env_secret_ref is not None
        secret = await s.get(Secret, server.env_secret_ref)
        assert secret is not None and secret.kind is SecretKind.mcp_env
        assert TOKEN.encode() not in secret.ciphertext
        assert await svc.open_env(s, server) == {"GITHUB_TOKEN": TOKEN}


async def test_headers_are_sealed_and_only_their_names_shown(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    aid = await _assistant(client, org_headers)
    r = await _create(client, org_headers, aid, **_http())
    assert r.status_code == 201, r.text
    assert r.json()["header_names"] == ["Authorization"]
    assert TOKEN not in r.text
    async with get_sessionmaker()() as s:
        server = await s.get(McpServer, uuid.UUID(r.json()["id"]))
        assert server is not None
        secret = await s.get(Secret, server.headers_secret_ref)
        assert secret is not None and secret.kind is SecretKind.mcp_headers
        assert await svc.open_headers(s, server) == {"Authorization": HEADER_VALUE}


# ── validation ───────────────────────────────────────────────


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        (_stdio(name="Files"), "lowercase letters"),
        (_stdio(name="my_files"), "lowercase letters"),  # `_` would blur mcp__x__y
        (_stdio(name="caps"), "reserved"),
        (_stdio(command=""), "needs a command"),
        (_stdio(url="https://x.example.com"), "not a URL"),
        (_stdio(args=["--token", TOKEN]), "looks like a credential"),
        (_http(url=None), "needs a url"),
        (_http(command="npx"), "takes a URL and headers"),
        (_http(url="http://mcp.example.com/mcp"), "must use https"),
        (_http(url="https://user:pw@mcp.example.com/"), "not in the URL"),
        (_http(url="https://mcp.example.com/mcp?api_key=abc"), "carry a credential"),
        (_http(headers={"Host": "evil"}), "set by the connection"),
        (_http(headers={"X-A": "one\r\nX-B: two"}), "spans lines"),
        (_stdio(env={"1BAD": "x"}), "not a valid environment variable"),
    ],
)
async def test_bad_registrations_are_refused_with_a_reason(
    client: AsyncClient, org_headers: dict[str, str], body: dict[str, Any], fragment: str
) -> None:
    aid = await _assistant(client, org_headers)
    r = await _create(client, org_headers, aid, **body)
    assert r.status_code == 422, r.text
    assert fragment in r.text


async def test_names_are_unique_per_assistant(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    aid = await _assistant(client, org_headers)
    assert (await _create(client, org_headers, aid, **_stdio())).status_code == 201
    again = await _create(client, org_headers, aid, **_stdio(env={}))
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "mcp_server_name_taken"
    other = await _assistant(client, org_headers)
    assert (await _create(client, org_headers, other, **_stdio())).status_code == 201


# ── updates ──────────────────────────────────────────────────


async def test_replacing_headers_drops_the_old_secret(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    aid = await _assistant(client, org_headers)
    sid = (await _create(client, org_headers, aid, **_http())).json()["id"]
    before = await _secret_count()
    url = f"/api/v1/assistants/{aid}/mcp-servers/{sid}"
    r = await client.patch(url, json={"headers": {"X-Api-Key": "k2"}}, headers=org_headers)
    assert r.status_code == 200, r.text
    assert r.json()["header_names"] == ["X-Api-Key"]
    assert await _secret_count() == before, "the old sealed row is deleted, not orphaned"

    cleared = await client.patch(url, json={"headers": {}}, headers=org_headers)
    assert cleared.json()["header_names"] == []
    assert await _secret_count() == before - 1


async def test_pointing_elsewhere_forgets_the_discovered_tools(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    aid = await _assistant(client, org_headers)
    sid = (await _create(client, org_headers, aid, **_http())).json()["id"]
    async with get_sessionmaker()() as s:
        server = await s.get(McpServer, uuid.UUID(sid))
        assert server is not None
        server.tools = [{"name": "search", "description": "", "input_schema": {}}]
        server.status = McpServerStatus.ok
        await s.commit()

    url = f"/api/v1/assistants/{aid}/mcp-servers/{sid}"
    renamed = await client.patch(url, json={"name": "tickets-v2"}, headers=org_headers)
    assert renamed.json()["tools"] != [], "a rename does not change what the server offers"
    moved = await client.patch(
        url, json={"url": "https://other.example.com/mcp"}, headers=org_headers
    )
    body = moved.json()
    assert body["tools"] == [] and body["status"] == "unknown"


async def test_an_update_cannot_mix_transports(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    aid = await _assistant(client, org_headers)
    sid = (await _create(client, org_headers, aid, **_http())).json()["id"]
    r = await client.patch(
        f"/api/v1/assistants/{aid}/mcp-servers/{sid}", json={"command": "npx"}, headers=org_headers
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "mcp_wrong_fields"


async def test_renaming_onto_another_server_conflicts(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    aid = await _assistant(client, org_headers)
    await _create(client, org_headers, aid, **_http())
    sid = (await _create(client, org_headers, aid, **_stdio())).json()["id"]
    r = await client.patch(
        f"/api/v1/assistants/{aid}/mcp-servers/{sid}", json={"name": "tickets"}, headers=org_headers
    )
    assert r.status_code == 409


# ── deletion ─────────────────────────────────────────────────


async def test_deleting_a_server_deletes_its_secrets(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    aid = await _assistant(client, org_headers)
    before = await _secret_count()
    sid = (await _create(client, org_headers, aid, **_stdio())).json()["id"]
    assert await _secret_count() == before + 1
    r = await client.delete(f"/api/v1/assistants/{aid}/mcp-servers/{sid}", headers=org_headers)
    assert r.status_code == 200
    assert await _secret_count() == before


async def test_deleting_the_assistant_deletes_its_servers_secrets(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    aid = await _assistant(client, org_headers)
    before = await _secret_count()
    await _create(client, org_headers, aid, **_stdio())
    await _create(client, org_headers, aid, **_http())
    assert await _secret_count() == before + 2
    r = await client.delete(f"/api/v1/assistants/{aid}", headers=org_headers)
    assert r.status_code in (200, 204), r.text
    assert await _secret_count() == before


# ── graph references ─────────────────────────────────────────


def _graph_with_mcp(server_id: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "nodes": [
            {"id": "in", "type": "input", "position": {"x": 0, "y": 0}, "data": {}},
            {"id": "ag", "type": "agent", "position": {"x": 200, "y": 0}, "data": {}},
            {"id": "out", "type": "output", "position": {"x": 400, "y": 0}, "data": {}},
            {
                "id": "mcp",
                "type": "mcp_server",
                "position": {"x": 0, "y": 150},
                "data": {"mcp_server_id": server_id},
            },
        ],
        "edges": [
            {"source": "in", "target": "ag"},
            {"source": "ag", "target": "out"},
            {"source": "mcp", "target": "ag"},
        ],
    }


async def _codes(client: AsyncClient, headers: dict[str, str], aid: str, graph: dict) -> set[str]:
    r = await client.put(f"/api/v1/assistants/{aid}/draft-graph", json=graph, headers=headers)
    assert r.status_code == 200, r.text
    return {e["code"] for e in r.json()["validation"]["errors"]}


async def test_a_graph_can_only_use_this_assistants_servers(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    mine = await _assistant(client, org_headers)
    theirs = await _assistant(client, org_headers)
    own = (await _create(client, org_headers, mine, **_http())).json()["id"]
    other = (await _create(client, org_headers, theirs, **_http())).json()["id"]

    assert "unknown_mcp_server" not in await _codes(client, org_headers, mine, _graph_with_mcp(own))
    for ref in (other, str(uuid.uuid4()), "not-a-uuid"):
        assert "unknown_mcp_server" in await _codes(
            client, org_headers, mine, _graph_with_mcp(ref)
        ), ref


async def test_the_audit_log_never_holds_a_value(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    from app.models.audit_log import AuditLog

    aid = await _assistant(client, org_headers)
    sid = (await _create(client, org_headers, aid, **_http())).json()["id"]
    await client.patch(
        f"/api/v1/assistants/{aid}/mcp-servers/{sid}",
        json={"headers": {"Authorization": "Bearer " + "b" * 40}},
        headers=org_headers,
    )
    async with get_sessionmaker()() as s:
        rows = (await s.scalars(select(AuditLog).where(AuditLog.target_type == "mcp_server"))).all()
    dumped = json.dumps([r.meta for r in rows])
    assert {r.action for r in rows} >= {"mcp_server.create", "mcp_server.update"}
    assert TOKEN not in dumped and "b" * 40 not in dumped


# ── sandbox limits (task 4.4) ────────────────────────────────


async def test_limits_are_for_local_commands_and_updates_merge(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    aid = await _assistant(client, org_headers)
    remote = await _create(client, org_headers, aid, **_http(sandbox={"memory_mb": 256}))
    assert remote.status_code == 422, "limits mean nothing for a remote server"

    created = await _create(client, org_headers, aid, **_stdio(sandbox={"memory_mb": 256}))
    assert created.status_code == 201, created.text
    assert created.json()["sandbox"]["memory_mb"] == 256
    assert created.json()["sandbox"]["cpu_seconds"] == 600, "unset limits take defaults"

    url = f"/api/v1/assistants/{aid}/mcp-servers/{created.json()['id']}"
    r = await client.patch(url, json={"sandbox": {"idle_timeout_s": 120}}, headers=org_headers)
    assert r.status_code == 200, r.text
    limits = r.json()["sandbox"]
    assert limits["idle_timeout_s"] == 120
    assert limits["memory_mb"] == 256, "an update of one limit keeps the others"

    too_big = await client.patch(url, json={"sandbox": {"memory_mb": 100_000}}, headers=org_headers)
    assert too_big.status_code == 422
