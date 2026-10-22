"""A Postgres connection scoped to the person asking (Phase 9.3).

With `scope_to_asker`, every statement runs inside a transaction that sets
`app.asker_email` to the signed-in person's email, and the database's own
row-level security policies decide what they see. Without a signed-in
person it refuses: an eval or a dry run never runs unscoped. Only Postgres
can do it. A fake adapter records what would reach the database; the last
test runs the whole thing against the dev Postgres, when it is up.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

import pytest
from app.agent import actions, caps_sql
from app.agent.actions import Asker
from app.datasources import ConnectionInfo, DbError, get_sql_adapter
from app.datasources.base import QueryResult
from app.datasources.postgres import rls_report
from app.datasources.sql_guard import Permissions, SqlBlocked, guard
from app.schemas.assistant_config import DatabaseRef
from httpx import AsyncClient
from tests.infra import unavailable

from test_orgs import _register  # type: ignore[import-not-found]

pytestmark = pytest.mark.anyio

API = "/api/v1"
PAT = Asker(uuid.uuid4(), "pat@example.com", "Pat")


class FakeAdapter:
    """Records each statement and who it was scoped to."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def run(self, info: ConnectionInfo, statement: str, **kw: Any) -> QueryResult:
        self.calls.append({"statement": statement, **kw})
        return QueryResult(columns=["id"], rows=[[1]], row_count=1)


async def _owner(client: AsyncClient) -> tuple[dict[str, str], str]:
    owner = await _register(client, "owner@example.com")
    org = (await client.post(f"{API}/orgs", json={"name": "Acme"}, headers=owner.headers)).json()
    h = {**owner.headers, "X-Org-Id": org["id"]}
    a = (await client.post(f"{API}/assistants", json={"name": "HR"}, headers=h)).json()
    return h, str(a["id"])


async def _postgres(client: AsyncClient, h: dict[str, str], aid: str, *, scoped: bool) -> str:
    r = await client.post(
        f"{API}/assistants/{aid}/db-connections",
        json={
            "name": "HR",
            "engine": "postgres",
            "host": "hr.corp.example.com",
            "port": 5432,
            "database": "hr",
            "username": "assistant",
            "password": "not-a-real-one",
            "permissions": {"read": True, "scope_to_asker": scoped},
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


def test_the_switch_is_off_unless_set() -> None:
    assert Permissions.from_dict({}).scope_to_asker is False
    assert Permissions.from_dict({"scope_to_asker": True}).scope_to_asker is True


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT set_config('app.asker_email', 'ceo@example.com', false)",
        "SET app.asker_email = 'ceo@example.com'",
        "SELECT current_setting('app.asker_email')",
    ],
)
def test_the_model_cant_change_who_is_asking(sql: str) -> None:
    with pytest.raises(SqlBlocked):
        guard(sql, engine="postgres", permissions=Permissions(read=True, scope_to_asker=True))


async def test_only_postgres_can_be_scoped(client: AsyncClient, tmp_path: Path) -> None:
    h, aid = await _owner(client)
    body = {
        "name": "Local",
        "engine": "sqlite",
        "database": str(tmp_path / "x.db"),
        "permissions": {"read": True, "scope_to_asker": True},
    }
    r = await client.post(f"{API}/assistants/{aid}/db-connections", json=body, headers=h)
    assert (r.status_code, r.json()["error"]["code"]) == (400, "scope_needs_postgres")

    body["permissions"] = {"read": True}
    made = await client.post(f"{API}/assistants/{aid}/db-connections", json=body, headers=h)
    assert made.status_code == 201, made.text
    r = await client.patch(
        f"{API}/assistants/{aid}/db-connections/{made.json()['id']}",
        json={"permissions": {"read": True, "scope_to_asker": True}},
        headers=h,
    )
    assert (r.status_code, r.json()["error"]["code"]) == (400, "scope_needs_postgres")


async def _query(aid: str, cid: str, asker: Asker | None) -> dict[str, Any]:
    tools = {
        t.name: t
        for t in caps_sql.build_sql_tools(uuid.UUID(aid), [DatabaseRef(connection_id=cid)], asker)
    }
    return await tools["sql_query"].handler({"connection_id": cid, "sql": "SELECT id FROM leave"})


async def test_a_scoped_query_carries_the_asker_and_refuses_without_one(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    h, aid = await _owner(client)
    cid = await _postgres(client, h, aid, scoped=True)
    fake = FakeAdapter()
    monkeypatch.setattr(caps_sql, "get_sql_adapter", lambda _engine: fake)

    nobody = await _query(aid, cid, None)
    assert nobody.get("is_error") and "signed-in person" in nobody["content"][0]["text"]
    assert fake.calls == [], "nothing reached the database"

    ok = await _query(aid, cid, PAT)
    assert not ok.get("is_error"), ok
    (call,) = fake.calls
    assert call["asker_email"] == "pat@example.com"


async def test_an_unscoped_connection_is_not_told_who_asks(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    h, aid = await _owner(client)
    cid = await _postgres(client, h, aid, scoped=False)
    fake = FakeAdapter()
    monkeypatch.setattr(caps_sql, "get_sql_adapter", lambda _engine: fake)
    await _query(aid, cid, PAT)
    (call,) = fake.calls
    assert call["asker_email"] is None


async def test_a_declared_action_on_a_scoped_connection_needs_an_asker(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import datasources

    h, aid = await _owner(client)
    cid = await _postgres(client, h, aid, scoped=True)
    fake = FakeAdapter()
    monkeypatch.setattr(datasources, "get_sql_adapter", lambda _engine: fake)

    refused = await actions.run_statement(uuid.UUID(aid), cid, "SELECT id FROM leave")
    assert refused.get("is_error") and "signed-in person" in refused["content"][0]["text"]
    await actions.run_statement(uuid.UUID(aid), cid, "SELECT id FROM leave", "pat@example.com")
    (call,) = fake.calls
    assert call["asker_email"] == "pat@example.com"


async def test_other_engines_refuse_to_be_scoped(tmp_path: Path) -> None:
    info = ConnectionInfo(engine="sqlite", database=str(tmp_path / "x.db"))
    with pytest.raises(DbError, match="scoped"):
        await get_sql_adapter("sqlite").run(
            info, "SELECT 1", timeout_ms=1000, max_rows=10, asker_email="pat@example.com"
        )
    with pytest.raises(DbError, match="scoped"):
        await get_sql_adapter("mysql").run(
            ConnectionInfo(engine="mysql", database="x", host="localhost"),
            "SELECT 1",
            timeout_ms=1000,
            max_rows=10,
            asker_email="pat@example.com",
        )


# ── against a real Postgres ──────────────────────────────────

PG = ConnectionInfo(
    engine="postgres",
    host="localhost",
    port=int(os.environ.get("PGPORT", "45432")),
    database="app",
    username="app",
    password="app",
)


async def _admin(sql: str) -> None:
    await get_sql_adapter("postgres").run(
        PG, sql, timeout_ms=5000, max_rows=10, mutating=True, returns_rows=False
    )


@pytest.mark.integration
async def test_row_level_security_sees_only_the_askers_rows() -> None:
    try:
        await get_sql_adapter("postgres").test(PG)
    except DbError as exc:  # pragma: no cover - environment-dependent
        unavailable("postgres", f"postgres not reachable: {exc}")

    suffix = uuid.uuid4().hex[:8]
    table, role, password = f"leave_{suffix}", f"reader_{suffix}", uuid.uuid4().hex
    reader = ConnectionInfo(
        engine="postgres",
        host=PG.host,
        port=PG.port,
        database=PG.database,
        username=role,
        password=password,
    )
    await _admin(f"CREATE TABLE {table} (id int, email text)")
    try:
        await _admin(f"INSERT INTO {table} VALUES (1, 'pat@example.com'), (2, 'sam@example.com')")
        await _admin(f"CREATE ROLE {role} LOGIN PASSWORD '{password}'")
        await _admin(f"GRANT SELECT ON {table} TO {role}")

        # No policy yet: the table is named as shared with everyone.
        problems, shared = await rls_report(reader)
        assert problems == [] and any(table in s for s in shared)

        await _admin(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        await _admin(
            f"CREATE POLICY mine ON {table} "
            "USING (email = current_setting('app.asker_email', true))"
        )
        problems, shared = await rls_report(reader)
        assert problems == [] and not any(table in s for s in shared)

        adapter = get_sql_adapter("postgres")
        sql = f"SELECT id FROM {table} ORDER BY id"
        pat = await adapter.run(
            reader, sql, timeout_ms=5000, max_rows=10, asker_email="pat@example.com"
        )
        assert pat.rows == [[1]]
        nobody = await adapter.run(reader, sql, timeout_ms=5000, max_rows=10)
        assert nobody.rows == [], "with no asker, the policy matches nothing"

        # The table's owner (and a superuser) isn't bound by the policy.
        owner_problems, _ = await rls_report(PG)
        assert owner_problems
    finally:
        await _admin(f"DROP TABLE IF EXISTS {table}")
        await _admin(f"DROP ROLE IF EXISTS {role}")
