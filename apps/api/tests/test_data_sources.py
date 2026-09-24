"""Data source CRUD (task 2.2). File upload hits real MinIO, so those cases
are marked ``integration``; url/text creation never touch storage."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


async def _new_assistant(client: AsyncClient, org_headers: dict[str, str]) -> str:
    r = await client.post("/api/v1/assistants", json={"name": "KB Bot"}, headers=org_headers)
    return r.json()["id"]


async def test_create_url_source(client: AsyncClient, org_headers: dict[str, str]) -> None:
    aid = await _new_assistant(client, org_headers)
    resp = await client.post(
        f"/api/v1/assistants/{aid}/data-sources",
        json={"type": "url", "name": "Docs site", "url": "https://example.com/docs"},
        headers=org_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["type"] == "url"
    assert body["uri"] == "https://example.com/docs"
    assert body["status"] == "pending"


async def test_create_text_source(client: AsyncClient, org_headers: dict[str, str]) -> None:
    aid = await _new_assistant(client, org_headers)
    resp = await client.post(
        f"/api/v1/assistants/{aid}/data-sources",
        json={"type": "text", "name": "Pasted FAQ", "text": "Q: hi? A: hello."},
        headers=org_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["type"] == "text"
    assert body["bytes"] == len(b"Q: hi? A: hello.")


async def test_list_get_and_delete(client: AsyncClient, org_headers: dict[str, str]) -> None:
    aid = await _new_assistant(client, org_headers)
    created = await client.post(
        f"/api/v1/assistants/{aid}/data-sources",
        json={"type": "text", "name": "one", "text": "content"},
        headers=org_headers,
    )
    sid = created.json()["id"]

    listing = await client.get(f"/api/v1/assistants/{aid}/data-sources", headers=org_headers)
    assert [s["id"] for s in listing.json()["items"]] == [sid]

    got = await client.get(f"/api/v1/assistants/{aid}/data-sources/{sid}", headers=org_headers)
    assert got.status_code == 200
    assert got.json()["name"] == "one"

    deleted = await client.delete(
        f"/api/v1/assistants/{aid}/data-sources/{sid}", headers=org_headers
    )
    assert deleted.status_code == 200

    missing = await client.get(f"/api/v1/assistants/{aid}/data-sources/{sid}", headers=org_headers)
    assert missing.status_code == 404


async def test_reindex_resets_status_to_pending(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    aid = await _new_assistant(client, org_headers)
    created = await client.post(
        f"/api/v1/assistants/{aid}/data-sources",
        json={"type": "text", "name": "one", "text": "content"},
        headers=org_headers,
    )
    sid = created.json()["id"]

    resp = await client.post(
        f"/api/v1/assistants/{aid}/data-sources/{sid}:reindex", headers=org_headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "pending"


async def test_cross_tenant_data_source_is_404(client: AsyncClient) -> None:
    async def signup(email: str) -> dict[str, str]:
        r = await client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": "supersecret", "name": "x"},
        )
        h = {"Authorization": f"Bearer {r.json()['access_token']}"}
        me = await client.get("/api/v1/auth/me", headers=h)
        return {**h, "X-Org-Id": me.json()["memberships"][0]["org_id"]}

    alice = await signup("alice-kb@example.com")
    bob = await signup("bob-kb@example.com")
    aid = await _new_assistant(client, alice)
    created = await client.post(
        f"/api/v1/assistants/{aid}/data-sources",
        json={"type": "text", "name": "secret", "text": "shh"},
        headers=alice,
    )
    sid = created.json()["id"]

    resp = await client.get(f"/api/v1/assistants/{aid}/data-sources/{sid}", headers=bob)
    assert resp.status_code == 404


@pytest.mark.integration
async def test_upload_file_source(client: AsyncClient, org_headers: dict[str, str]) -> None:
    aid = await _new_assistant(client, org_headers)
    resp = await client.post(
        f"/api/v1/assistants/{aid}/data-sources/upload",
        files={"file": ("handbook.txt", b"employee handbook contents", "text/plain")},
        headers=org_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["type"] == "file"
    assert body["name"] == "handbook.txt"
    assert body["bytes"] == len(b"employee handbook contents")

    # deleting a file source must also remove the MinIO object, not just the row
    deleted = await client.delete(
        f"/api/v1/assistants/{aid}/data-sources/{body['id']}", headers=org_headers
    )
    assert deleted.status_code == 200


# ── citation deep links (task 2.9) ───────────────────────────


async def test_content_url_for_a_url_source_is_the_original_address(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    aid = await _new_assistant(client, org_headers)
    sid = (
        await client.post(
            f"/api/v1/assistants/{aid}/data-sources",
            json={"type": "url", "name": "Docs", "url": "https://example.com/docs"},
            headers=org_headers,
        )
    ).json()["id"]
    body = (
        await client.get(
            f"/api/v1/assistants/{aid}/data-sources/{sid}/content-url", headers=org_headers
        )
    ).json()
    assert body == {"url": "https://example.com/docs", "expires_in": 0, "kind": "url"}


async def test_content_url_for_pasted_text_has_nothing_to_open(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    """A dead link is worse than an honest "no target" — the panel shows the
    snippet instead."""
    aid = await _new_assistant(client, org_headers)
    sid = (
        await client.post(
            f"/api/v1/assistants/{aid}/data-sources",
            json={"type": "text", "name": "Pasted", "text": "some pasted policy"},
            headers=org_headers,
        )
    ).json()["id"]
    body = (
        await client.get(
            f"/api/v1/assistants/{aid}/data-sources/{sid}/content-url", headers=org_headers
        )
    ).json()
    assert body["url"] is None
    assert body["kind"] == "none"


async def test_content_url_is_tenant_scoped(client: AsyncClient) -> None:
    """Knowing a data source id must not be enough to mint a link to another
    org's object."""

    async def signup(email: str) -> dict[str, str]:
        r = await client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": "supersecret", "name": "x"},
        )
        h = {"Authorization": f"Bearer {r.json()['access_token']}"}
        me = await client.get("/api/v1/auth/me", headers=h)
        return {**h, "X-Org-Id": me.json()["memberships"][0]["org_id"]}

    alice = await signup("alice-cite@example.com")
    bob = await signup("bob-cite@example.com")
    aid = await _new_assistant(client, alice)
    sid = (
        await client.post(
            f"/api/v1/assistants/{aid}/data-sources",
            json={"type": "url", "name": "Docs", "url": "https://example.com/docs"},
            headers=alice,
        )
    ).json()["id"]

    resp = await client.get(f"/api/v1/assistants/{aid}/data-sources/{sid}/content-url", headers=bob)
    assert resp.status_code == 404


async def test_presigned_urls_are_signed_against_the_browser_reachable_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inside Docker the API reaches MinIO at http://minio:9000, which no
    browser can resolve. The host is part of the SigV4 signature, so it cannot
    be rewritten after signing — it has to be signed against the public
    endpoint from the start."""
    from urllib.parse import urlparse

    from app.config import settings
    from app.storage import s3

    monkeypatch.setattr(settings, "s3_endpoint", "http://minio:9000")
    monkeypatch.setattr(settings, "s3_public_endpoint", "http://localhost:9000")
    s3.get_s3_presign_client.cache_clear()
    s3.get_s3_client.cache_clear()
    try:
        url = await s3.presigned_get_url("data-sources/x/y/handbook.pdf")
        assert urlparse(url).netloc == "localhost:9000"
        assert "X-Amz-Signature" in url
    finally:
        s3.get_s3_presign_client.cache_clear()
        s3.get_s3_client.cache_clear()


@pytest.mark.integration
async def test_content_url_for_a_file_actually_fetches_the_object(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    """The assertion that matters. ``generate_presigned_url`` never contacts
    the server — it is local HMAC signing — so a test that only checks a
    ``url`` key came back passes with MinIO stopped AND with the signature
    wrong. Only fetching it proves the link works."""
    from httpx import AsyncClient as RawClient

    content = b"employee handbook contents for deep linking"
    aid = await _new_assistant(client, org_headers)
    sid = (
        await client.post(
            f"/api/v1/assistants/{aid}/data-sources/upload",
            files={"file": ("handbook.txt", content, "text/plain")},
            headers=org_headers,
        )
    ).json()["id"]

    body = (
        await client.get(
            f"/api/v1/assistants/{aid}/data-sources/{sid}/content-url", headers=org_headers
        )
    ).json()
    assert body["kind"] == "file"
    assert body["expires_in"] > 0

    # Real network, not the ASGI transport, and deliberately no auth header —
    # this is exactly how a browser follows a citation's "Open" link.
    async with RawClient(timeout=10.0) as raw:
        fetched = await raw.get(body["url"])
    assert fetched.status_code == 200, fetched.text
    assert fetched.content == content


# ── counts for the sources UI (task 2.12) ────────────────────
#
# These stay in the unit tier: counts_for is plain SQL with no pgvector in
# sight, and a Chunk row round-trips on SQLite (embedding is nullable), so
# real Postgres buys nothing here.


async def _seed_index(
    db_session: AsyncSession, source_id: str, *, documents: int, chunks_per_doc: int
) -> None:
    from app.models.rag import Chunk, DataSource, Document

    source = await db_session.get(DataSource, uuid.UUID(source_id))
    assert source is not None
    for d in range(documents):
        doc = Document(
            data_source_id=source.id,
            assistant_id=source.assistant_id,
            org_id=source.org_id,
            title=f"doc {d}",
        )
        db_session.add(doc)
        await db_session.flush()
        for c in range(chunks_per_doc):
            db_session.add(
                Chunk(
                    document_id=doc.id,
                    assistant_id=source.assistant_id,
                    org_id=source.org_id,
                    ordinal=c,
                    content=f"chunk {c} of doc {d}",
                )
            )
    await db_session.commit()


async def _add_text_source(
    client: AsyncClient, org_headers: dict[str, str], aid: str, name: str
) -> str:
    r = await client.post(
        f"/api/v1/assistants/{aid}/data-sources",
        json={"type": "text", "name": name, "text": f"content for {name}"},
        headers=org_headers,
    )
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def test_a_freshly_created_source_reports_zero_counts(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    aid = await _new_assistant(client, org_headers)
    await _add_text_source(client, org_headers, aid, "one")
    (row,) = (
        await client.get(f"/api/v1/assistants/{aid}/data-sources", headers=org_headers)
    ).json()["items"]
    assert row["document_count"] == 0
    assert row["chunk_count"] == 0


async def test_counts_reflect_what_ingestion_produced(
    client: AsyncClient, org_headers: dict[str, str], db_session: AsyncSession
) -> None:
    aid = await _new_assistant(client, org_headers)
    sid = await _add_text_source(client, org_headers, aid, "handbook")
    await _seed_index(db_session, sid, documents=2, chunks_per_doc=3)

    (row,) = (
        await client.get(f"/api/v1/assistants/{aid}/data-sources", headers=org_headers)
    ).json()["items"]
    assert row["document_count"] == 2
    # 2 documents x 3 chunks. The outer join multiplies document rows, so a
    # naive count(*) would report 6 documents too.
    assert row["chunk_count"] == 6

    single = (
        await client.get(f"/api/v1/assistants/{aid}/data-sources/{sid}", headers=org_headers)
    ).json()
    assert (single["document_count"], single["chunk_count"]) == (2, 6)


async def test_a_document_with_no_chunks_counts_zero_chunks_not_one(
    client: AsyncClient, org_headers: dict[str, str], db_session: AsyncSession
) -> None:
    """The count(Chunk.id) vs count(*) distinction — a source that indexed
    into an empty document is 'ready' but has nothing searchable, and the UI
    warns about exactly that."""
    aid = await _new_assistant(client, org_headers)
    sid = await _add_text_source(client, org_headers, aid, "empty")
    await _seed_index(db_session, sid, documents=1, chunks_per_doc=0)

    (row,) = (
        await client.get(f"/api/v1/assistants/{aid}/data-sources", headers=org_headers)
    ).json()["items"]
    assert (row["document_count"], row["chunk_count"]) == (1, 0)


async def test_counts_do_not_bleed_between_sources(
    client: AsyncClient, org_headers: dict[str, str], db_session: AsyncSession
) -> None:
    aid = await _new_assistant(client, org_headers)
    a = await _add_text_source(client, org_headers, aid, "alpha")
    b = await _add_text_source(client, org_headers, aid, "beta")
    await _seed_index(db_session, a, documents=1, chunks_per_doc=4)
    await _seed_index(db_session, b, documents=1, chunks_per_doc=1)

    rows = (await client.get(f"/api/v1/assistants/{aid}/data-sources", headers=org_headers)).json()[
        "items"
    ]
    by_id = {r["id"]: r for r in rows}
    assert by_id[a]["chunk_count"] == 4
    assert by_id[b]["chunk_count"] == 1


async def test_deleting_a_source_removes_it_from_the_listing(
    client: AsyncClient, org_headers: dict[str, str], db_session: AsyncSession
) -> None:
    aid = await _new_assistant(client, org_headers)
    sid = await _add_text_source(client, org_headers, aid, "doomed")
    await _seed_index(db_session, sid, documents=1, chunks_per_doc=2)

    assert (
        await client.delete(f"/api/v1/assistants/{aid}/data-sources/{sid}", headers=org_headers)
    ).status_code == 200
    rows = (await client.get(f"/api/v1/assistants/{aid}/data-sources", headers=org_headers)).json()[
        "items"
    ]
    assert rows == []


async def test_a_source_being_indexed_shows_its_progress(
    client: AsyncClient, org_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.db.session import get_sessionmaker
    from app.models.rag import DataSource, DataSourceStatus

    aid = await _new_assistant(client, org_headers)
    r = await client.post(
        f"/api/v1/assistants/{aid}/data-sources",
        json={"type": "text", "name": "notes", "text": "hello"},
        headers=org_headers,
    )
    sid = uuid.UUID(r.json()["id"])
    async with get_sessionmaker()() as s:
        row = await s.get(DataSource, sid)
        assert row is not None
        row.status = DataSourceStatus.processing
        await s.commit()

    async def read(ids: list[uuid.UUID]) -> dict[uuid.UUID, dict[str, object]]:
        return {i: {"stage": "embedding", "done": 3, "total": 12} for i in ids}

    monkeypatch.setattr("app.api.routes.data_sources.ingest_progress.read", read)
    listing = await client.get(f"/api/v1/assistants/{aid}/data-sources", headers=org_headers)
    (item,) = listing.json()["items"]
    assert item["progress"] == {"stage": "embedding", "done": 3, "total": 12}
