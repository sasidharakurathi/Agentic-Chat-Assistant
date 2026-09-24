"""Which database tools are offered, and what they declare (tasks 3.6 / 1.4).

- A Postgres-only assistant was offered `mongo_find`/`mongo_aggregate` (and a
  Mongo-only one the SQL tools), learning the mismatch by calling them.
- `CapabilityTool.read_only` was declared on every tool and dropped on the way
  into the SDK: no MCP annotations reached the model.
- A Mongo *projection* was never screened, and it accepts `$function`.
"""

from __future__ import annotations

import uuid
from typing import Any, ClassVar

import pytest
from app.agent.caps import annotations_for
from app.agent.events import TokenEvent, UsageEvent
from app.agent.options import RuntimeSpec, build_runtime_spec
from app.datasources import ConnectionInfo, get_mongo_adapter
from app.datasources.mongodb import MongoBlocked
from app.schemas.assistant_config import AssistantConfig
from httpx import AsyncClient
from tests.test_chat import _new_assistant, _new_conversation

pytestmark = pytest.mark.anyio

A, B = str(uuid.uuid4()), str(uuid.uuid4())


def _config(*ids: str) -> AssistantConfig:
    return AssistantConfig.model_validate({"databases": [{"connection_id": i} for i in ids]})


def _names(spec: RuntimeSpec) -> set[str]:
    return {t.name for t in spec.caps_tools}


SQL = {"sql_list_schemas", "sql_introspect", "sql_query"}
MONGO = {"mongo_find", "mongo_aggregate"}


def test_a_postgres_only_assistant_gets_no_mongo_tools() -> None:
    spec = build_runtime_spec(_config(A), assistant_id=uuid.uuid4(), db_engines={A: "postgres"})
    assert _names(spec) >= SQL and not MONGO & _names(spec)


def test_a_mongo_only_assistant_gets_no_sql_tools() -> None:
    spec = build_runtime_spec(_config(A), assistant_id=uuid.uuid4(), db_engines={A: "mongodb"})
    assert _names(spec) >= MONGO and not SQL & _names(spec)


def test_both_kinds_wired_get_both() -> None:
    spec = build_runtime_spec(
        _config(A, B), assistant_id=uuid.uuid4(), db_engines={A: "mysql", B: "mongodb"}
    )
    assert _names(spec) >= (SQL | MONGO)


def test_annotations_state_what_a_tool_may_do() -> None:
    writable = AssistantConfig.model_validate(
        {"databases": [{"connection_id": A, "expose_write": True}]}
    )
    spec = build_runtime_spec(writable, assistant_id=uuid.uuid4(), db_engines={A: "postgres"})
    by_name = {t.name: t for t in spec.caps_tools}
    query = annotations_for(by_name["sql_query"])
    assert query.read_only_hint is False and query.destructive_hint is True

    # With no database exposing writes, sql_query genuinely cannot write.
    readonly = build_runtime_spec(_config(A), assistant_id=uuid.uuid4(), db_engines={A: "postgres"})
    ro = annotations_for({t.name: t for t in readonly.caps_tools}["sql_query"])
    assert ro.read_only_hint is True and ro.destructive_hint is False

    listing = annotations_for(by_name["sql_list_schemas"])
    assert listing.read_only_hint is True and listing.destructive_hint is False
    assert listing.open_world_hint is False


@pytest.mark.parametrize(
    "projection",
    [
        {"x": {"$function": {"body": "function() { return 1 }", "args": [], "lang": "js"}}},
        {"nested": {"deeper": {"$where": "sleep(10000)"}}},
    ],
)
async def test_a_projection_cannot_run_javascript(projection: dict[str, Any]) -> None:
    """Refused before any network call: no Mongo server needed."""
    info = ConnectionInfo(engine="mongodb", database="x", host="127.0.0.1", port=1)
    with pytest.raises(MongoBlocked):
        await get_mongo_adapter().find(
            info, collection="c", query={}, projection=projection, limit=5, timeout_ms=100
        )


class _SpecRecorder:
    name = "recorder"
    spec: ClassVar[RuntimeSpec | None] = None

    async def stream(self, *, spec: RuntimeSpec, **_: Any) -> Any:
        _SpecRecorder.spec = spec
        yield TokenEvent(text="ok")
        yield UsageEvent(tokens_in=1, tokens_out=1)


async def test_the_runtime_resolves_engines_from_the_wired_connections(
    client: AsyncClient, org_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.agent.runtime.get_driver", _SpecRecorder)
    aid = await _new_assistant(client, org_headers)
    conn = await client.post(
        f"/api/v1/assistants/{aid}/db-connections",
        json={"name": "PG", "engine": "postgres", "host": "db", "database": "x"},
        headers=org_headers,
    )
    cid = conn.json()["id"]
    cfg = (await client.get(f"/api/v1/assistants/{aid}", headers=org_headers)).json()[
        "draft_config"
    ]
    cfg["databases"] = [{"connection_id": cid}]
    await client.put(f"/api/v1/assistants/{aid}/draft-config", json=cfg, headers=org_headers)
    conv = await _new_conversation(client, org_headers, aid)

    from app.db.session import get_sessionmaker
    from app.services import chat as chat_svc

    async with get_sessionmaker()() as session:
        async for _ in chat_svc.run_message(session, conversation_id=uuid.UUID(conv), text="hi"):
            pass
    assert _SpecRecorder.spec is not None
    names = _names(_SpecRecorder.spec)
    assert names >= SQL and not MONGO & names
