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
        url = await s3.presigned_get_url(
            "data-sources/x/y/handbook.pdf",
            content_type="application/pdf",
            disposition='inline; filename="handbook.pdf"',
        )
        assert urlparse(url).netloc == "localhost:9000"
        assert "X-Amz-Signature" in url
        # The type and disposition are signed into the link (Phase 7a.1).
        assert "response-content-type=application%2Fpdf" in url
        assert "response-content-disposition=inline" in url
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


# ── uploaded files never run as the app (Phase 7a.1) ─────────
#
# Found in the gap review: the object was stored with the browser's type and
# the link served it on the app's own origin, so an HTML file with a script
# ran as the app when a citation was opened. These go through real MinIO,
# because only the store's answer proves the signed overrides are honoured.

SCRIPT_PAGE = (
    b"<html><body><script>alert(localStorage['as.access'])</script>VPN guide</body></html>"
)


async def _upload(
    client: AsyncClient, headers: dict[str, str], aid: str, name: str, body: bytes, ctype: str
):
    return await client.post(
        f"/api/v1/assistants/{aid}/data-sources/upload",
        files={"file": (name, body, ctype)},
        headers=headers,
    )


async def _follow_link(client: AsyncClient, headers: dict[str, str], aid: str, sid: str):
    from httpx import AsyncClient as RawClient

    link = await client.get(
        f"/api/v1/assistants/{aid}/data-sources/{sid}/content-url", headers=headers
    )
    async with RawClient(timeout=10.0) as raw:
        return await raw.get(link.json()["url"])


@pytest.mark.integration
async def test_an_html_upload_is_served_as_plain_text(
    client: AsyncClient, org_headers: dict[str, str], db_session: AsyncSession
) -> None:
    from app.models.rag import DataSource

    aid = await _new_assistant(client, org_headers)
    created = await _upload(client, org_headers, aid, "vpn.html", SCRIPT_PAGE, "text/html")
    assert created.status_code == 201, created.text
    # Stored as what it is, so ingest still parses it as HTML.
    source = await db_session.get(DataSource, uuid.UUID(created.json()["id"]))
    assert source is not None and source.config["content_type"] == "text/html"

    fetched = await _follow_link(client, org_headers, aid, created.json()["id"])
    assert fetched.status_code == 200
    assert fetched.headers["content-type"].startswith("text/plain")
    assert fetched.headers["content-disposition"].startswith("inline")
    assert fetched.content == SCRIPT_PAGE


@pytest.mark.integration
async def test_a_false_type_claim_changes_nothing(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    """The attack as a builder would try it: HTML bytes claiming to be a PDF,
    under a PDF name. The bytes decide; the link serves plain text."""
    aid = await _new_assistant(client, org_headers)
    created = await _upload(client, org_headers, aid, "policy.pdf", SCRIPT_PAGE, "application/pdf")
    assert created.status_code == 201, created.text
    # The object itself carries the server's type, not the claim.
    from app.config import settings
    from app.storage import get_s3_client

    listed = get_s3_client().list_objects_v2(
        Bucket=settings.s3_bucket, Prefix=f"data-sources/{aid}/{created.json()['id']}/"
    )
    key = listed["Contents"][0]["Key"]
    head = get_s3_client().head_object(Bucket=settings.s3_bucket, Key=key)
    assert head["ContentType"] == "text/plain"
    fetched = await _follow_link(client, org_headers, aid, created.json()["id"])
    assert fetched.headers["content-type"].startswith("text/plain")


@pytest.mark.integration
async def test_a_word_file_downloads_and_a_pdf_opens(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", "<w:document/>")
    aid = await _new_assistant(client, org_headers)
    docx = await _upload(
        client, org_headers, aid, "Leave policy é.docx", buf.getvalue(), "application/octet-stream"
    )
    pdf = await _upload(
        client, org_headers, aid, "handbook.pdf", b"%PDF-1.4\n%%EOF", "application/octet-stream"
    )
    assert docx.status_code == pdf.status_code == 201

    got_docx = await _follow_link(client, org_headers, aid, docx.json()["id"])
    assert got_docx.headers["content-disposition"].startswith("attachment")
    assert (
        "filename*=UTF-8''Leave%20policy%20%C3%A9.docx" in got_docx.headers["content-disposition"]
    )
    got_pdf = await _follow_link(client, org_headers, aid, pdf.json()["id"])
    assert got_pdf.headers["content-type"] == "application/pdf"
    assert got_pdf.headers["content-disposition"].startswith("inline")


@pytest.mark.integration
async def test_a_file_stored_before_the_fix_is_served_safely(
    client: AsyncClient, org_headers: dict[str, str], db_session: AsyncSession
) -> None:
    """Rows written by the old code hold whatever the browser claimed. The link
    must not trust it: simulate one by writing the old type back."""
    from app.models.rag import DataSource
    from app.storage import put_object

    aid = await _new_assistant(client, org_headers)
    created = await _upload(client, org_headers, aid, "old.html", SCRIPT_PAGE, "text/html")
    source = await db_session.get(DataSource, uuid.UUID(created.json()["id"]))
    assert source is not None and source.object_key
    await put_object(source.object_key, SCRIPT_PAGE, "text/html")  # the old storage
    fetched = await _follow_link(client, org_headers, aid, created.json()["id"])
    assert fetched.headers["content-type"].startswith("text/plain")


@pytest.mark.integration
async def test_a_binary_that_is_no_supported_format_is_refused(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    aid = await _new_assistant(client, org_headers)
    resp = await _upload(
        client, org_headers, aid, "tool.exe", b"MZ\x90\x00\x03\x00\x00\x00", "text/plain"
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "unsupported_file_type"
    assert "PDF, Word" in resp.json()["error"]["message"]


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
