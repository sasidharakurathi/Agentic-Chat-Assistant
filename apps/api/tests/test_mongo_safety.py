"""The Mongo tools honour the permission profile (P0-5b/c/d).

`caps_sql` runs every statement through `sql_guard` with the connection's
permissions. `caps_mongo` never looked at them: a `read: false` connection
still returned documents, a denied collection was readable just by naming it,
`limit=0` (which pymongo reads as *no limit*) materialised whole collections
in the API process, and the pool keyed clients without the connection URI,
so two connections differing only in credentials shared one client.

The tool handlers run for real against real connection rows; only the Mongo
adapter is replaced, by a recorder — so "refused" here means the adapter was
never reached.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from app.agent.caps_mongo import build_mongo_tools
from app.datasources.base import ConnectionInfo, QueryResult
from app.datasources.mongodb import MongoAdapter, MongoBlocked
from app.datasources.pool import _key
from app.schemas.assistant_config import DatabaseRef
from httpx import AsyncClient

pytestmark = pytest.mark.anyio


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def find(self, _info: Any, **kw: Any) -> QueryResult:
        self.calls.append({"op": "find", **kw})
        return QueryResult(columns=["_id"], rows=[["1"]], row_count=1)

    async def aggregate(self, _info: Any, **kw: Any) -> QueryResult:
        self.calls.append({"op": "aggregate", **kw})
        return QueryResult(columns=["_id"], rows=[["1"]], row_count=1)


@pytest.fixture
def adapter(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    rec = _Recorder()
    monkeypatch.setattr("app.agent.caps_mongo.get_mongo_adapter", lambda: rec)
    return rec


async def _tools(
    client: AsyncClient, headers: dict[str, str], **permissions: Any
) -> tuple[dict[str, Any], str]:
    a = (await client.post("/api/v1/assistants", json={"name": "M"}, headers=headers)).json()
    body = {
        "name": "Mongo",
        "engine": "mongodb",
        "host": "mongo",
        "database": "appdb",
        "permissions": {"read": True, "row_limit": 50, **permissions},
    }
    r = await client.post(
        f"/api/v1/assistants/{a['id']}/db-connections", json=body, headers=headers
    )
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    tools = build_mongo_tools(uuid.UUID(a["id"]), [DatabaseRef(connection_id=cid)])
    return {t.name: t.handler for t in tools}, cid


async def _find(handlers: dict[str, Any], cid: str, **args: Any) -> dict[str, Any]:
    return await handlers["mongo_find"]({"connection_id": cid, "collection": "orders", **args})


async def _agg(handlers: dict[str, Any], cid: str, pipeline: list, **args: Any) -> dict[str, Any]:
    return await handlers["mongo_aggregate"](
        {"connection_id": cid, "collection": "orders", "pipeline": pipeline, **args}
    )


# ── permissions ──────────────────────────────────────────────


async def test_a_connection_that_cannot_read_returns_nothing(
    client: AsyncClient, org_headers: dict[str, str], adapter: _Recorder
) -> None:
    handlers, cid = await _tools(client, org_headers, read=False)
    out = await _find(handlers, cid)
    assert out.get("is_error") is True
    assert adapter.calls == []


@pytest.mark.parametrize("collection", ["salaries", "Salaries", "appdb.salaries"])
async def test_a_denied_collection_cannot_be_read_by_naming_it(
    client: AsyncClient, org_headers: dict[str, str], adapter: _Recorder, collection: str
) -> None:
    handlers, cid = await _tools(client, org_headers, deny_tables=["salaries"])
    out = await handlers["mongo_find"]({"connection_id": cid, "collection": collection})
    assert out.get("is_error") is True
    assert "salaries" in out["content"][0]["text"].lower()
    assert adapter.calls == []


async def test_an_allow_list_is_exhaustive(
    client: AsyncClient, org_headers: dict[str, str], adapter: _Recorder
) -> None:
    handlers, cid = await _tools(client, org_headers, allow_tables=["orders"])
    assert (await _find(handlers, cid)).get("is_error") is not True
    out = await handlers["mongo_find"]({"connection_id": cid, "collection": "customers"})
    assert out.get("is_error") is True
    assert len(adapter.calls) == 1


@pytest.mark.parametrize("collection", ["system.users", "system.js", "system.profile"])
async def test_system_collections_are_refused(
    client: AsyncClient, org_headers: dict[str, str], adapter: _Recorder, collection: str
) -> None:
    """`system.js` holds stored server-side JavaScript; `system.profile`
    records other people's queries."""
    handlers, cid = await _tools(client, org_headers)
    out = await handlers["mongo_find"]({"connection_id": cid, "collection": collection})
    assert out.get("is_error") is True
    assert adapter.calls == []


@pytest.mark.parametrize(
    "pipeline",
    [
        [{"$lookup": {"from": "salaries", "localField": "a", "foreignField": "b", "as": "s"}}],
        [
            {
                "$graphLookup": {
                    "from": "salaries",
                    "startWith": "$a",
                    "connectFromField": "a",
                    "connectToField": "b",
                    "as": "s",
                }
            }
        ],
        [{"$unionWith": "salaries"}],
        [{"$unionWith": {"coll": "salaries", "pipeline": []}}],
        # nested: a lookup inside a facet, and one inside another lookup
        [{"$facet": {"x": [{"$lookup": {"from": "salaries", "pipeline": [], "as": "s"}}]}}],
        [
            {
                "$lookup": {
                    "from": "orders",
                    "as": "o",
                    "pipeline": [{"$lookup": {"from": "salaries", "pipeline": [], "as": "s"}}],
                }
            }
        ],
    ],
)
async def test_a_denied_collection_cannot_be_joined_in(
    client: AsyncClient, org_headers: dict[str, str], adapter: _Recorder, pipeline: list
) -> None:
    """Checking only the tool's own `collection` argument would leave every
    join as a way round the deny-list."""
    handlers, cid = await _tools(client, org_headers, deny_tables=["salaries"])
    out = await _agg(handlers, cid, pipeline)
    assert out.get("is_error") is True
    assert adapter.calls == []


async def test_a_cross_database_lookup_is_refused(
    client: AsyncClient, org_headers: dict[str, str], adapter: _Recorder
) -> None:
    handlers, cid = await _tools(client, org_headers)
    pipeline = [
        {"$lookup": {"from": {"db": "admin", "coll": "system.users"}, "as": "u", "pipeline": []}}
    ]
    assert (await _agg(handlers, cid, pipeline)).get("is_error") is True
    assert adapter.calls == []


@pytest.mark.parametrize("stage", ["$currentOp", "$listSessions", "$planCacheStats"])
async def test_cluster_introspection_stages_are_refused(
    client: AsyncClient, org_headers: dict[str, str], adapter: _Recorder, stage: str
) -> None:
    """The Mongo equivalent of reading `pg_stat_activity`: other sessions'
    operations and queries."""
    handlers, cid = await _tools(client, org_headers)
    assert (await _agg(handlers, cid, [{stage: {}}])).get("is_error") is True
    assert adapter.calls == []


async def test_an_ordinary_read_still_works(
    client: AsyncClient, org_headers: dict[str, str], adapter: _Recorder
) -> None:
    handlers, cid = await _tools(client, org_headers, deny_tables=["salaries"])
    out = await _agg(
        handlers,
        cid,
        [{"$lookup": {"from": "customers", "localField": "c", "foreignField": "_id", "as": "x"}}],
    )
    assert out.get("is_error") is not True
    assert adapter.calls[0]["op"] == "aggregate"


# ── limits ───────────────────────────────────────────────────


@pytest.mark.parametrize("limit", [0, -5, -100000, True, 10.7, "100000", None, 10**9])
async def test_the_limit_always_lands_inside_the_cap(
    client: AsyncClient, org_headers: dict[str, str], adapter: _Recorder, limit: object
) -> None:
    """pymongo reads 0 as *no limit* and a negative as "one batch of |n|";
    `isinstance(True, int)` is True in Python. Every one of these used to
    reach the server as something other than a bounded positive limit."""
    handlers, cid = await _tools(client, org_headers)
    await _find(handlers, cid, limit=limit)
    await _agg(handlers, cid, [{"$match": {}}], limit=limit)
    for call in adapter.calls:
        assert 1 <= call["limit"] <= 50, (limit, call["limit"])


async def test_the_adapter_itself_refuses_an_unbounded_limit() -> None:
    """Defence in depth under the tool: the adapter never sends 0 or a
    negative to the server, whoever calls it."""
    info = ConnectionInfo(engine="mongodb", database="x", host="nowhere.invalid")
    for limit in (0, -1):
        with pytest.raises(MongoBlocked):
            await MongoAdapter().find(
                info, collection="c", query={}, projection=None, limit=limit, timeout_ms=100
            )
        with pytest.raises(MongoBlocked):
            await MongoAdapter().aggregate(
                info, collection="c", pipeline=[], limit=limit, timeout_ms=100
            )


# ── pooling ──────────────────────────────────────────────────


def test_connections_differing_only_in_their_uri_get_separate_clients() -> None:
    """The client *is* the authenticated session. Keyed without the URI, the
    second connection silently reused the first one's credentials."""
    a = ConnectionInfo(engine="mongodb", database="x", options={"uri": "mongodb://alice:pw1@h"})
    b = ConnectionInfo(engine="mongodb", database="x", options={"uri": "mongodb://bob:pw2@h"})
    assert _key(a) != _key(b)


def test_the_pool_key_does_not_contain_the_uri_itself() -> None:
    """Keys live in process memory and could reach a log line."""
    info = ConnectionInfo(engine="mongodb", database="x", options={"uri": "mongodb://u:pw@h"})
    assert "pw" not in _key(info)
