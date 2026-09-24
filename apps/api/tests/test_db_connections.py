"""Database-connection credentials never come back out (P0-5a).

`DbConnectionSummary` was designed so a password *cannot* be returned: it
has no field to carry one. But MongoDB is configured with a connection
string, and the only place to put one was `options["uri"]` — stored as
plaintext JSONB and echoed verbatim on every list/get/create/update. The
envelope encryption was correct; this path simply never reached it.

Now a URI goes in through a write-only `connection_uri`, is sealed exactly
like a password, and `options` refuses to hold one.
"""

from __future__ import annotations

import json
import uuid

import pytest
from app.db.session import get_sessionmaker
from app.models.integration import DbConnection
from app.models.secret import Secret, SecretKind
from app.services import db_connections as db_svc
from httpx import AsyncClient

pytestmark = pytest.mark.anyio

URI = "mongodb://reporting:hunter2-s3cret@mongo.internal:27017/?authSource=admin"


async def _assistant(client: AsyncClient, headers: dict[str, str]) -> str:
    r = await client.post("/api/v1/assistants", json={"name": "DB"}, headers=headers)
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def _create(client: AsyncClient, headers: dict[str, str], aid: str, **body: object):
    payload = {"name": "Mongo", "engine": "mongodb", "database": "appdb", **body}
    return await client.post(
        f"/api/v1/assistants/{aid}/db-connections", json=payload, headers=headers
    )


async def test_a_connection_uri_is_accepted_and_never_returned(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    aid = await _assistant(client, org_headers)
    r = await _create(client, org_headers, aid, connection_uri=URI)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["has_connection_uri"] is True
    for leaked in ("hunter2", "mongodb://", "reporting:"):
        assert leaked not in json.dumps(body), f"{leaked!r} came back in the create response"

    listed = await client.get(f"/api/v1/assistants/{aid}/db-connections", headers=org_headers)
    got = await client.get(
        f"/api/v1/assistants/{aid}/db-connections/{body['id']}", headers=org_headers
    )
    for resp in (listed, got):
        assert "hunter2" not in resp.text


async def test_the_uri_is_sealed_at_rest_and_opened_only_for_the_adapter(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    aid = await _assistant(client, org_headers)
    cid = uuid.UUID((await _create(client, org_headers, aid, connection_uri=URI)).json()["id"])

    async with get_sessionmaker()() as s:
        conn = await s.get(DbConnection, cid)
        assert conn is not None
        assert "uri" not in (conn.options or {}), "plaintext options must not hold the URI"
        assert conn.uri_secret_ref is not None
        secret = await s.get(Secret, conn.uri_secret_ref)
        assert secret is not None
        assert secret.kind is SecretKind.db_connection_uri
        assert b"hunter2" not in secret.ciphertext

        info = await db_svc.connection_info(s, conn)
        assert info.options["uri"] == URI, "decrypted at the last moment, for the adapter only"


async def test_a_uri_in_plaintext_options_is_refused(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    """Refused rather than silently moved: `options` is shown to anyone who
    can view the connection, and whoever put a secret there should learn
    that it belongs somewhere else."""
    aid = await _assistant(client, org_headers)
    r = await _create(client, org_headers, aid, host="mongo", options={"uri": URI})
    assert r.status_code == 422
    assert "connection_uri" in r.text
    assert "hunter2" not in r.text, "the refusal must not echo the secret back either"


async def test_no_validation_error_anywhere_echoes_a_password(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    """Found alongside the above: pydantic puts the *whole request body* in a
    missing-field or model-level error's `input`, and the 422 handler
    returned it verbatim — so any malformed request that carried a password
    got it echoed back, on every route."""
    r = await client.post("/api/v1/auth/register", json={"password": "hunter2-s3cret"})
    assert r.status_code == 422
    assert "hunter2" not in r.text
    assert r.json()["error"]["details"]["errors"], "the errors themselves are still reported"


async def test_a_model_level_validation_error_is_a_422_not_a_crash(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    """Pre-existing, found while testing the above. A model validator that
    raises ValueError puts the exception object into the error's `ctx`; the
    422 handler serialised errors with plain JSON and crashed on it, so
    e.g. `ddl` without `write` — a validator that predates this change —
    never got its 422 (live, the client got no response at all)."""
    aid = await _assistant(client, org_headers)
    r = await client.post(
        f"/api/v1/assistants/{aid}/db-connections",
        json={
            "name": "x",
            "engine": "postgres",
            "host": "h",
            "database": "d",
            "permissions": {"read": True, "write": False, "ddl": True},
        },
        headers=org_headers,
    )
    assert r.status_code == 422
    assert "ddl requires write" in r.text


async def test_a_connection_uri_is_only_for_mongodb(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    aid = await _assistant(client, org_headers)
    r = await client.post(
        f"/api/v1/assistants/{aid}/db-connections",
        json={
            "name": "PG",
            "engine": "postgres",
            "host": "db",
            "database": "x",
            "connection_uri": "postgresql://u:p@db/x",
        },
        headers=org_headers,
    )
    assert r.status_code == 422


async def test_replacing_a_credential_deletes_the_old_ciphertext(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    """The old row used to be left behind "so an in-flight query doesn't
    break" — but an in-flight query already holds the decrypted value, so
    all the leftover row did was accumulate unreferenced ciphertext."""
    aid = await _assistant(client, org_headers)
    cid = (await _create(client, org_headers, aid, connection_uri=URI)).json()["id"]
    async with get_sessionmaker()() as s:
        old = (await s.get(DbConnection, uuid.UUID(cid))).uri_secret_ref  # type: ignore[union-attr]

    r = await client.patch(
        f"/api/v1/assistants/{aid}/db-connections/{cid}",
        json={"connection_uri": URI.replace("hunter2", "rotated")},
        headers=org_headers,
    )
    assert r.status_code == 200, r.text

    async with get_sessionmaker()() as s:
        conn = await s.get(DbConnection, uuid.UUID(cid))
        assert conn is not None and conn.uri_secret_ref != old
        assert await s.get(Secret, old) is None, "the replaced secret is gone"
        info = await db_svc.connection_info(s, conn)
        assert "rotated" in info.options["uri"]


async def test_deleting_the_connection_deletes_both_secrets(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    aid = await _assistant(client, org_headers)
    cid = (await _create(client, org_headers, aid, connection_uri=URI)).json()["id"]
    async with get_sessionmaker()() as s:
        ref = (await s.get(DbConnection, uuid.UUID(cid))).uri_secret_ref  # type: ignore[union-attr]
    r = await client.delete(f"/api/v1/assistants/{aid}/db-connections/{cid}", headers=org_headers)
    assert r.status_code in (200, 204), r.text
    async with get_sessionmaker()() as s:
        assert await s.get(Secret, ref) is None
