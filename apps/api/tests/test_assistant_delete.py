"""Deleting an assistant (F-4).

There was no DELETE for an assistant at all — no route, no service, no
client call — so nothing an assistant owned could ever be torn down. Three
things need more than the database's own cascade:

- **credentials**: `db_connections -> secrets` is `SET NULL` the other way
  round, so the cascade would have orphaned every sealed password and URI;
- **uploaded files** live in object storage, not Postgres;
- **usage history** used to *cascade*, so the moment delete existed it would
  have erased spend that genuinely happened from the org's billing totals.
  It now survives, with the assistant reference nulled.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from app.db.session import get_sessionmaker
from app.models.conversation import Conversation, Message
from app.models.integration import DbConnection
from app.models.secret import Secret
from app.models.usage import UsageEvent
from app.services import chat as chat_svc
from httpx import AsyncClient
from sqlalchemy import func, select
from tests.test_rbac_escalation import _team

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def fake_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    """Uploads must really succeed here, or the object-storage assertions
    below would have nothing to prove. The unit tier has no MinIO or queue."""

    async def put(_key: str, _body: bytes, _content_type: str) -> None:
        return None

    async def enqueue(*_a: Any, **_k: Any) -> bool:
        return True

    monkeypatch.setattr("app.services.data_sources.put_object", put)
    monkeypatch.setattr("app.services.data_sources.enqueue_ingest", enqueue)


@pytest.fixture
def deleted_objects(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    keys: list[str] = []

    async def record(key: str) -> None:
        keys.append(key)

    monkeypatch.setattr("app.services.assistants.delete_object", record)
    return keys


async def _populated(client: AsyncClient, headers: dict[str, str]) -> dict[str, Any]:
    """An assistant owning one of everything worth tearing down."""
    aid = (
        await client.post("/api/v1/assistants", json={"name": "Doomed"}, headers=headers)
    ).json()["id"]
    conn = await client.post(
        f"/api/v1/assistants/{aid}/db-connections",
        json={
            "name": "PG",
            "engine": "postgres",
            "host": "db",
            "database": "x",
            "password": "pw-to-be-deleted",
        },
        headers=headers,
    )
    assert conn.status_code == 201, conn.text
    mongo = await client.post(
        f"/api/v1/assistants/{aid}/db-connections",
        json={
            "name": "M",
            "engine": "mongodb",
            "database": "x",
            "connection_uri": "mongodb://u:p@h",
        },
        headers=headers,
    )
    assert mongo.status_code == 201, mongo.text
    upload = await client.post(
        f"/api/v1/assistants/{aid}/data-sources/upload",
        files={"file": ("notes.txt", b"hello world", "text/plain")},
        headers=headers,
    )
    conv = uuid.UUID(
        (
            await client.post(f"/api/v1/assistants/{aid}/conversations", json={}, headers=headers)
        ).json()["id"]
    )
    async with get_sessionmaker()() as session:
        [e async for e in chat_svc.run_message(session, conversation_id=conv, text="hello")]
    assert upload.status_code == 201, upload.text
    return {"aid": aid, "conv": conv}


async def _counts(aid: str) -> dict[str, int]:
    a = uuid.UUID(aid)
    async with get_sessionmaker()() as s:
        return {
            "conversations": await s.scalar(
                select(func.count()).select_from(Conversation).where(Conversation.assistant_id == a)
            ),
            "connections": await s.scalar(
                select(func.count()).select_from(DbConnection).where(DbConnection.assistant_id == a)
            ),
            "secrets": await s.scalar(select(func.count()).select_from(Secret)),
        }


async def test_deleting_an_assistant_tears_everything_down(
    client: AsyncClient, org_headers: dict[str, str], deleted_objects: list[str]
) -> None:
    thing = await _populated(client, org_headers)
    before = await _counts(thing["aid"])
    assert before["connections"] == 2 and before["secrets"] >= 2

    r = await client.delete(f"/api/v1/assistants/{thing['aid']}", headers=org_headers)
    assert r.status_code == 200, r.text

    assert (
        await client.get(f"/api/v1/assistants/{thing['aid']}", headers=org_headers)
    ).status_code == 404
    after = await _counts(thing["aid"])
    assert (after["conversations"], after["connections"]) == (0, 0)
    assert after["secrets"] == before["secrets"] - 2, (
        "both sealed credentials are gone, not orphaned"
    )
    async with get_sessionmaker()() as s:
        msgs = await s.scalar(
            select(func.count())
            .select_from(Message)
            .where(Message.conversation_id == thing["conv"])
        )
    assert msgs == 0
    assert len(deleted_objects) == 1, "the uploaded file is removed from object storage"


async def test_spend_survives_the_assistant_it_was_incurred_by(
    client: AsyncClient, org_headers: dict[str, str], deleted_objects: list[str]
) -> None:
    """The usage ledger is append-only: the model calls happened, and an org's
    totals must not drop because an assistant was tidied away."""
    thing = await _populated(client, org_headers)
    async with get_sessionmaker()() as s:
        before = await s.scalar(select(func.count()).select_from(UsageEvent))
    await client.delete(f"/api/v1/assistants/{thing['aid']}", headers=org_headers)
    async with get_sessionmaker()() as s:
        rows = (await s.scalars(select(UsageEvent))).all()
    assert len(rows) == before, "no spend was erased"
    # The only assistant in this test's org is gone, so every row now points
    # at nothing — and still counts towards the org's totals.
    assert all(r.assistant_id is None and r.conversation_id is None for r in rows)


async def test_the_delete_is_audited(
    client: AsyncClient, org_headers: dict[str, str], deleted_objects: list[str]
) -> None:
    thing = await _populated(client, org_headers)
    await client.delete(f"/api/v1/assistants/{thing['aid']}", headers=org_headers)
    org_id = org_headers["X-Org-Id"]
    log = (await client.get(f"/api/v1/orgs/{org_id}/audit-log", headers=org_headers)).json()[
        "items"
    ]
    entry = next(e for e in log if e["action"] == "assistant.delete")
    assert entry["target_id"] == thing["aid"]
    assert entry["meta"]["name"] == "Doomed"


async def test_only_the_creator_or_an_admin_may_delete(
    client: AsyncClient, deleted_objects: list[str]
) -> None:
    t = await _team(client)
    aid = (
        await client.post("/api/v1/assistants", json={"name": "Owned"}, headers=t.h(t.member))
    ).json()["id"]
    assert (
        await client.delete(f"/api/v1/assistants/{aid}", headers=t.h(t.member2))
    ).status_code == 403
    assert (
        await client.delete(f"/api/v1/assistants/{aid}", headers=t.h(t.admin))
    ).status_code == 200


async def test_an_object_store_failure_does_not_undo_the_delete(
    client: AsyncClient, org_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Storage is cleaned up *after* the commit: a storage outage leaves an
    orphaned file (logged), never database rows pointing at deleted files."""

    async def broken(_key: str) -> None:
        raise RuntimeError("minio is down")

    monkeypatch.setattr("app.services.assistants.delete_object", broken)
    thing = await _populated(client, org_headers)
    r = await client.delete(f"/api/v1/assistants/{thing['aid']}", headers=org_headers)
    assert r.status_code == 200
    assert (
        await client.get(f"/api/v1/assistants/{thing['aid']}", headers=org_headers)
    ).status_code == 404
