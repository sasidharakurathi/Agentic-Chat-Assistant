"""Graph references are checked against what actually exists (F-6, F-10).

The graph validator is deliberately pure — no database — so nothing ever
checked a node's *references*. A `database` node could name a connection that
did not exist, belonged to another assistant, or was not even a UUID (that one
published cleanly and then raised ValueError at the start of every turn). And
`expose_write` could be switched on for a read-only connection, which the task
row explicitly forbids: every write it invited was approve-then-refused.

The references are now checked in the service, where the database is: errors
land on the offending node, the draft still saves, and publishing is refused.
Because a connection's permissions can change *after* publish, the permission
router also consults the live credential before asking anyone to approve.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from app.agent.approvals import ApprovalRequest, build_can_use_tool
from app.schemas.assistant_config import ApprovalPolicy, DatabaseRef
from claude_agent_sdk import PermissionResultDeny
from httpx import AsyncClient

pytestmark = pytest.mark.anyio


def _graph(**db: Any) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = [
        {"id": "in", "type": "input", "position": {"x": 0, "y": 0}, "data": {}},
        {"id": "ag", "type": "agent", "position": {"x": 200, "y": 0}, "data": {}},
        {"id": "out", "type": "output", "position": {"x": 400, "y": 0}, "data": {}},
    ]
    edges = [{"source": "in", "target": "ag"}, {"source": "ag", "target": "out"}]
    if db:
        nodes.append({"id": "db", "type": "database", "position": {"x": 0, "y": 150}, "data": db})
        edges.append({"source": "db", "target": "ag"})
    return {"schema_version": 1, "nodes": nodes, "edges": edges}


async def _assistant(client: AsyncClient, headers: dict[str, str]) -> str:
    r = await client.post("/api/v1/assistants", json={"name": "Ref"}, headers=headers)
    return str(r.json()["id"])


async def _connection(
    client: AsyncClient, headers: dict[str, str], aid: str, *, write: bool
) -> str:
    r = await client.post(
        f"/api/v1/assistants/{aid}/db-connections",
        json={
            "name": "PG",
            "engine": "postgres",
            "host": "db",
            "database": "x",
            "permissions": {"read": True, "write": write},
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def _save(client: AsyncClient, headers: dict[str, str], aid: str, graph: dict) -> dict:
    r = await client.put(f"/api/v1/assistants/{aid}/draft-graph", json=graph, headers=headers)
    assert r.status_code == 200, "the draft always saves; problems are reported, not refused"
    return r.json()


def _codes(result: dict) -> dict[str, str | None]:
    return {e["code"]: e.get("node_id") for e in result["validation"]["errors"]}


async def test_expose_write_on_a_read_only_connection_is_an_error(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    aid = await _assistant(client, org_headers)
    cid = await _connection(client, org_headers, aid, write=False)
    saved = await _save(client, org_headers, aid, _graph(connection_id=cid, expose_write=True))
    assert _codes(saved).get("expose_write_without_write") == "db"


async def test_expose_write_on_a_writable_connection_is_fine(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    aid = await _assistant(client, org_headers)
    cid = await _connection(client, org_headers, aid, write=True)
    saved = await _save(client, org_headers, aid, _graph(connection_id=cid, expose_write=True))
    assert saved["validation"]["errors"] == []


@pytest.mark.parametrize("ref", ["not-a-uuid", str(uuid.uuid4())])
async def test_a_connection_that_does_not_exist_is_an_error(
    client: AsyncClient, org_headers: dict[str, str], ref: str
) -> None:
    """The non-UUID case used to publish cleanly and then raise ValueError at
    the start of every turn."""
    aid = await _assistant(client, org_headers)
    saved = await _save(client, org_headers, aid, _graph(connection_id=ref))
    assert _codes(saved).get("unknown_connection") == "db"


async def test_another_assistants_connection_is_not_this_ones(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    mine = await _assistant(client, org_headers)
    theirs = await _assistant(client, org_headers)
    cid = await _connection(client, org_headers, theirs, write=True)
    saved = await _save(client, org_headers, mine, _graph(connection_id=cid))
    assert _codes(saved).get("unknown_connection") == "db"


async def test_an_unknown_data_source_is_an_error(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    aid = await _assistant(client, org_headers)
    graph = _graph()
    graph["nodes"] += [
        {"id": "kb", "type": "knowledge_base", "position": {"x": 0, "y": 300}, "data": {}},
        {
            "id": "ds",
            "type": "data_source",
            "position": {"x": 0, "y": 450},
            "data": {"data_source_id": str(uuid.uuid4())},
        },
    ]
    graph["edges"] += [{"source": "ds", "target": "kb"}, {"source": "kb", "target": "ag"}]
    saved = await _save(client, org_headers, aid, graph)
    assert _codes(saved).get("unknown_data_source") == "ds"


async def test_publishing_is_refused_while_a_reference_is_wrong(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    aid = await _assistant(client, org_headers)
    cid = await _connection(client, org_headers, aid, write=False)
    await _save(client, org_headers, aid, _graph(connection_id=cid, expose_write=True))
    r = await client.post(f"/api/v1/assistants/{aid}/versions", json={}, headers=org_headers)
    assert r.status_code == 400
    assert "expose_write_without_write" in r.text


async def test_the_detail_view_reports_it_too(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    """The canvas reloads from the detail endpoint, so the error must survive
    a page refresh, not only appear in the save response."""
    aid = await _assistant(client, org_headers)
    await _save(client, org_headers, aid, _graph(connection_id="not-a-uuid"))
    detail = (await client.get(f"/api/v1/assistants/{aid}", headers=org_headers)).json()
    assert "unknown_connection" in {e["code"] for e in detail["draft_validation"]["errors"]}


# ── runtime: the credential can change after publish ─────────


async def test_a_write_the_credential_cannot_make_is_denied_without_asking() -> None:
    """Published with writes allowed; someone then made the connection
    read-only. The router must not interrupt a human for a write the guard
    is certain to refuse."""
    asked: list[str] = []

    async def request(_t: str, _i: dict, _r: str, why: str, **_k: object) -> str:
        asked.append(why)
        return "approved"

    async def credential_allows(_connection_id: str, kind: str) -> bool:
        return kind == "read"

    can_use = build_can_use_tool(
        ApprovalPolicy(),
        ApprovalRequest(conversation_id=uuid.uuid4(), org_id=uuid.uuid4(), request=request),
        [DatabaseRef(connection_id="c1", expose_write=True)],
        credential_allows=credential_allows,
    )
    result = await can_use(
        "mcp__caps__sql_query", {"connection_id": "c1", "sql": "UPDATE t SET a=1"}, None
    )
    assert asked == []
    assert isinstance(result, PermissionResultDeny)
    assert "read-only" in result.message


async def test_a_write_the_credential_can_make_still_asks() -> None:
    asked: list[str] = []

    async def request(_t: str, _i: dict, _r: str, why: str, **_k: object) -> str:
        asked.append(why)
        return "denied"

    async def credential_allows(_connection_id: str, _kind: str) -> bool:
        return True

    can_use = build_can_use_tool(
        ApprovalPolicy(),
        ApprovalRequest(conversation_id=uuid.uuid4(), org_id=uuid.uuid4(), request=request),
        [DatabaseRef(connection_id="c1", expose_write=True)],
        credential_allows=credential_allows,
    )
    await can_use("mcp__caps__sql_query", {"connection_id": "c1", "sql": "UPDATE t SET a=1"}, None)
    assert asked == ["UPDATE t SET a=1"]


async def test_a_config_save_reports_validation_too(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    """The Panels tab saves through draft-config. It returned no validation,
    so after a Panels edit the error badges showed the previous canvas
    save's result."""
    aid = await _assistant(client, org_headers)
    cid = await _connection(client, org_headers, aid, write=False)
    cfg = (await client.get(f"/api/v1/assistants/{aid}", headers=org_headers)).json()[
        "draft_config"
    ]
    cfg["databases"] = [{"connection_id": cid, "expose_write": True}]
    r = await client.put(f"/api/v1/assistants/{aid}/draft-config", json=cfg, headers=org_headers)
    assert r.status_code == 200, r.text
    codes = {e["code"] for e in r.json()["validation"]["errors"]}
    assert "expose_write_without_write" in codes
