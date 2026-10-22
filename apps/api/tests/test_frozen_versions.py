"""An approved version is frozen (Phase 7b.1, plan D16).

What a version uses beyond its config (its knowledge, database connections,
MCP servers and tools) is recorded when it is submitted. Chat answers from
the knowledge it was approved with while previews see the new copy; a
connection, server or tool changed since approval is left out of its turns;
approving or rolling back refuses while anything has drifted; each run
records the manifest it answered with.
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from typing import Any

import pytest
from app.agent.events import TokenEvent, UsageEvent
from app.agent.options import build_runtime_spec
from app.db.session import get_sessionmaker
from app.models.assistant import AssistantVersion
from app.models.audit_log import AuditLog
from app.models.conversation import Run
from app.models.integration import McpServer, McpTransport
from app.models.rag import Chunk, DataSource, DataSourceStatus, Document
from app.rag import ingest
from app.rag.retrieve import retrieve
from app.schemas.assistant_config import AssistantConfig, RagRetrieval
from app.services import chat as chat_svc
from app.services import manifest
from httpx import AsyncClient
from sqlalchemy import select
from tests import lexical_rag
from tests.versions import PREVIEW, go_live, submit

pytestmark = pytest.mark.anyio

API = "/api/v1"


class _ToolSpy:
    """Records the tools each turn was offered."""

    name = "spy"

    def __init__(self) -> None:
        self.tools: list[list[str]] = []

    async def stream(self, *, spec: Any, **_: Any) -> Any:
        self.tools.append(list(spec.enabled_tools))
        yield TokenEvent(text="ok")
        yield UsageEvent(tokens_in=1, tokens_out=1)


async def _assistant(client: AsyncClient, h: dict[str, str], **config: Any) -> str:
    a = (await client.post(f"{API}/assistants", json={"name": "Desk"}, headers=h)).json()
    cfg = a["draft_config"]
    cfg.update(config)
    r = await client.put(f"{API}/assistants/{a['id']}/draft-config", json=cfg, headers=h)
    assert r.status_code == 200, r.text
    return str(a["id"])


async def _source(client: AsyncClient, h: dict[str, str], aid: str, name: str = "Policy") -> str:
    r = await client.post(
        f"{API}/assistants/{aid}/data-sources",
        json={"type": "text", "name": name, "text": "placeholder"},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def _index(source_id: str, text: str) -> uuid.UUID:
    """What a (re)index leaves behind, through the same keep-or-drop step
    the ingestion job uses: one document with one chunk."""
    checksum = f"sha-{text}"
    async with get_sessionmaker()() as s:
        source = await s.get(DataSource, uuid.UUID(source_id))
        assert source is not None
        doc_id, _ = await ingest._drop_previous_index(s, source, checksum)
        s.add(
            Document(
                id=doc_id,
                data_source_id=source.id,
                assistant_id=source.assistant_id,
                org_id=source.org_id,
                title=source.name,
                checksum=checksum,
            )
        )
        await s.flush()
        s.add(
            Chunk(
                document_id=doc_id,
                assistant_id=source.assistant_id,
                org_id=source.org_id,
                ordinal=0,
                content=text,
                chunk_metadata={"start": 0, "end": len(text)},
            )
        )
        source.status = DataSourceStatus.ready
        await s.commit()
        return doc_id


async def _version(aid: str, number: int) -> AssistantVersion:
    async with get_sessionmaker()() as s:
        v = await s.scalar(
            select(AssistantVersion).where(
                AssistantVersion.assistant_id == uuid.UUID(aid),
                AssistantVersion.version_number == number,
            )
        )
        assert v is not None
        return v


async def _search(aid: str, number: int | None, query: str) -> list[str]:
    """What kb_search finds on version `number`, or the draft (None)."""
    async with get_sessionmaker()() as s:
        documents = None
        if number is not None:
            v = await _version(aid, number)
            _, frozen = await manifest.frozen_for(s, v, AssistantConfig.model_validate(v.config))
            documents = frozen.documents
        hits = await retrieve(
            s,
            assistant_id=uuid.UUID(aid),
            query=query,
            config=RagRetrieval(min_score=0.0),
            documents=documents,
        )
        return [h.content for h in hits]


async def _turn(cid: str) -> None:
    async with get_sessionmaker()() as session:
        async for _ in chat_svc.run_message(session, conversation_id=uuid.UUID(cid), text="hi"):
            pass


def _sqlite(tmp_path: Path) -> str:
    path = tmp_path / f"{uuid.uuid4().hex}.db"
    sqlite3.connect(path).close()
    return str(path)


async def _connection(client: AsyncClient, h: dict[str, str], aid: str, db: str) -> str:
    r = await client.post(
        f"{API}/assistants/{aid}/db-connections",
        json={"name": "Tickets", "engine": "sqlite", "database": db},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


# ── knowledge: the approved copy ─────────────────────────────


async def test_chat_keeps_the_approved_copy_while_a_preview_sees_the_new_one(
    client: AsyncClient, org_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    lexical_rag.install(monkeypatch)
    aid = await _assistant(client, org_headers, rag={"enabled": True})
    sid = await _source(client, org_headers, aid)
    approved = await _index(sid, "Refunds take 14 days to arrive.")
    await go_live(client, aid, org_headers)
    v1 = await _version(aid, 1)
    assert v1.manifest is not None
    assert [d["id"] for d in v1.manifest["knowledge"]["documents"]] == [str(approved)]

    # The builder reindexes with a new policy.
    newer = await _index(sid, "Refunds take 30 days to arrive.")
    async with get_sessionmaker()() as s:
        kept = await s.get(Document, approved)
        assert kept is not None and kept.superseded_at is not None, "kept beside the new one"

    assert await _search(aid, 1, "how long do refunds take") == ["Refunds take 14 days to arrive."]
    assert await _search(aid, None, "how long do refunds take") == [
        "Refunds take 30 days to arrive."
    ], "the draft sees only the current copy"
    listed = (await client.get(f"{API}/assistants/{aid}/data-sources", headers=org_headers)).json()
    assert [s["document_count"] for s in listed["items"]] == [1], "the source holds one copy"

    # Approved: the new copy goes live.
    await go_live(client, aid, org_headers)
    assert await _search(aid, 2, "how long do refunds take") == ["Refunds take 30 days to arrive."]
    v2 = await _version(aid, 2)
    assert v2.manifest is not None
    assert [d["id"] for d in v2.manifest["knowledge"]["documents"]] == [str(newer)]


async def test_old_copies_are_kept_for_the_live_and_previous_versions_only(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    aid = await _assistant(client, org_headers, rag={"enabled": True})
    sid = await _source(client, org_headers, aid)
    a = await _index(sid, "Policy A.")
    await go_live(client, aid, org_headers)
    b = await _index(sid, "Policy B.")
    await go_live(client, aid, org_headers)
    c = await _index(sid, "Policy C.")  # v2 (live) keeps B; v1 (previous) keeps A
    async with get_sessionmaker()() as s:
        assert set(await s.scalars(select(Document.id))) == {a, b, c}

    await go_live(client, aid, org_headers)  # v3 live (C), v2 previous (B)
    async with get_sessionmaker()() as s:
        removed = await manifest.sweep_superseded(s)
        await s.commit()
        assert removed == 1
        assert set(await s.scalars(select(Document.id))) == {b, c}

    # v1's copy is gone: rolling back to it is refused, not silently empty.
    r = await client.post(f"{API}/assistants/{aid}/versions/1/make-live", headers=org_headers)
    assert (r.status_code, r.json()["error"]["code"]) == (409, "version_drifted")
    r = await client.post(f"{API}/assistants/{aid}/versions/2/make-live", headers=org_headers)
    assert r.status_code == 200, r.text


async def test_a_source_the_live_version_answers_from_cannot_be_deleted(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    aid = await _assistant(client, org_headers, rag={"enabled": True})
    used = await _source(client, org_headers, aid, "Used")
    await _index(used, "Approved text.")
    await go_live(client, aid, org_headers)
    later = await _source(client, org_headers, aid, "Added later")

    r = await client.delete(f"{API}/assistants/{aid}/data-sources/{used}", headers=org_headers)
    assert (r.status_code, r.json()["error"]["code"]) == (409, "source_in_use")
    assert r.json()["error"]["details"] == {"versions": [1]}
    r = await client.delete(f"{API}/assistants/{aid}/data-sources/{later}", headers=org_headers)
    assert r.status_code == 200, "not in what was approved"


async def test_a_version_approved_with_no_knowledge_searches_nothing(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    aid = await _assistant(client, org_headers, rag={"enabled": True})
    await go_live(client, aid, org_headers)
    v1 = await _version(aid, 1)
    async with get_sessionmaker()() as s:
        config, frozen = await manifest.frozen_for(s, v1, AssistantConfig.model_validate(v1.config))
    assert frozen.documents == frozenset()
    assert config.rag.enabled is False, "an empty source list would mean every source"


# ── connections and MCP tools: fail closed on drift ──────────


async def test_a_connection_changed_since_approval_is_left_out_of_chat(
    client: AsyncClient,
    org_headers: dict[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = _ToolSpy()
    monkeypatch.setattr("app.agent.runtime.get_driver", lambda: spy)
    aid = await _assistant(client, org_headers)
    conn = await _connection(client, org_headers, aid, _sqlite(tmp_path))
    cfg = (await client.get(f"{API}/assistants/{aid}", headers=org_headers)).json()["draft_config"]
    cfg["databases"] = [{"connection_id": conn}]
    await client.put(f"{API}/assistants/{aid}/draft-config", json=cfg, headers=org_headers)
    await go_live(client, aid, org_headers)
    cid = (
        await client.post(f"{API}/assistants/{aid}/conversations", json={}, headers=org_headers)
    ).json()["id"]
    await _turn(cid)
    assert any("sql" in t for t in spy.tools[-1]), "the approved connection is offered"

    # Pointed at another database after approval.
    r = await client.patch(
        f"{API}/assistants/{aid}/db-connections/{conn}",
        json={"database": _sqlite(tmp_path)},
        headers=org_headers,
    )
    assert r.status_code == 200, r.text
    await _turn(cid)
    assert not any("sql" in t for t in spy.tools[-1]), "changed since approval: not offered"
    drift = (
        await client.get(f"{API}/assistants/{aid}/versions/1/drift", headers=org_headers)
    ).json()
    assert [(d["kind"], d["what"], d["name"]) for d in drift["drift"]] == [
        ("database", "changed", "Tickets")
    ]

    # A preview of the draft uses the connection as it is now.
    preview = (
        await client.post(
            f"{API}/assistants/{aid}/conversations", json=PREVIEW, headers=org_headers
        )
    ).json()["id"]
    await _turn(preview)
    assert any("sql" in t for t in spy.tools[-1])


async def test_approving_refuses_a_version_whose_connection_changed_after_submission(
    client: AsyncClient, org_headers: dict[str, str], tmp_path: Path
) -> None:
    aid = await _assistant(client, org_headers)
    conn = await _connection(client, org_headers, aid, _sqlite(tmp_path))
    cfg = (await client.get(f"{API}/assistants/{aid}", headers=org_headers)).json()["draft_config"]
    cfg["databases"] = [{"connection_id": conn}]
    await client.put(f"{API}/assistants/{aid}/draft-config", json=cfg, headers=org_headers)
    n = await submit(client, aid, org_headers)
    await client.patch(
        f"{API}/assistants/{aid}/db-connections/{conn}",
        json={"permissions": {"read": True, "write": True}},
        headers=org_headers,
    )
    r = await client.post(
        # With the note the sole owner's own approval needs (7b.3): the drift
        # is what refuses it.
        f"{API}/assistants/{aid}/versions/{n}/approve",
        json={"note": "Tested in preview"},
        headers=org_headers,
    )
    assert (r.status_code, r.json()["error"]["code"]) == (409, "version_drifted")
    assert "Tickets" in r.json()["error"]["message"]

    # Submitted again, as things are now: approvable.
    await go_live(client, aid, org_headers)


async def test_an_mcp_tool_or_server_changed_since_approval_is_dropped(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    aid = await _assistant(client, org_headers)
    tools = [
        {
            "name": "lookup",
            "description": "Looks up an order",
            "input_schema": {},
            "read_only": True,
        },
        {"name": "refund", "description": "Refunds an order", "input_schema": {}},
    ]
    async with get_sessionmaker()() as s:
        server = McpServer(
            assistant_id=uuid.UUID(aid),
            org_id=uuid.UUID(org_headers["X-Org-Id"]),
            name="shop",
            transport=McpTransport.http,
            url="https://mcp.example.com/mcp",
            tools=tools,
        )
        s.add(server)
        await s.commit()
        server_id = str(server.id)
    cfg = (await client.get(f"{API}/assistants/{aid}", headers=org_headers)).json()["draft_config"]
    cfg["mcp_servers"] = [{"id": server_id, "tools": ["lookup", "refund"]}]
    r = await client.put(f"{API}/assistants/{aid}/draft-config", json=cfg, headers=org_headers)
    assert r.status_code == 200, r.text
    await go_live(client, aid, org_headers)

    async def offered() -> list[list[str]]:
        v = await _version(aid, 1)
        async with get_sessionmaker()() as s:
            config, _ = await manifest.frozen_for(s, v, AssistantConfig.model_validate(v.config))
        return [ref.tools for ref in config.mcp_servers]

    assert await offered() == [["lookup", "refund"]]

    # Re-discovery now says `refund` is read-only: it would skip approval.
    async with get_sessionmaker()() as s:
        row = await s.get(McpServer, uuid.UUID(server_id))
        assert row is not None
        row.tools = [tools[0], {**tools[1], "read_only": True}]
        await s.commit()
    assert await offered() == [["lookup"]]

    # The server now points somewhere else: none of it.
    async with get_sessionmaker()() as s:
        row = await s.get(McpServer, uuid.UUID(server_id))
        assert row is not None
        row.url = "https://elsewhere.example.com/mcp"
        await s.commit()
    assert await offered() == []


# ── what each run answered with ──────────────────────────────


async def test_each_run_records_its_manifest_and_an_old_version_gets_one(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    aid = await _assistant(client, org_headers)
    await go_live(client, aid, org_headers)
    v1 = await _version(aid, 1)
    # A version from before the gate: no manifest yet.
    async with get_sessionmaker()() as s:
        row = await s.get(AssistantVersion, v1.id)
        assert row is not None
        row.manifest, row.manifest_hash = None, None
        await s.commit()

    cid = (
        await client.post(f"{API}/assistants/{aid}/conversations", json={}, headers=org_headers)
    ).json()["id"]
    await _turn(cid)

    v1 = await _version(aid, 1)
    assert v1.manifest is not None and v1.manifest_hash == manifest.digest(v1.manifest)
    async with get_sessionmaker()() as s:
        run = await s.scalar(select(Run).where(Run.conversation_id == uuid.UUID(cid)))
        assert run is not None and run.manifest_hash == v1.manifest_hash
        recorded = await s.scalar(
            select(AuditLog.action).where(AuditLog.action == "assistant.version.manifest_recorded")
        )
    assert recorded == "assistant.version.manifest_recorded"
    detail = (await client.get(f"{API}/assistants/{aid}/versions/1", headers=org_headers)).json()
    assert detail["manifest_hash"] == v1.manifest_hash
    assert set(detail["manifest"]) == {
        "v",
        "knowledge",
        "databases",
        "mcp_servers",
        "http_connections",
    }


async def test_the_search_tool_on_a_version_searches_its_approved_copy(
    client: AsyncClient, org_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Through the turn's own tool, as the runtime builds it: the knowledge
    a version was approved with reaches kb_search."""
    lexical_rag.install(monkeypatch)
    aid = await _assistant(client, org_headers, rag={"enabled": True})
    sid = await _source(client, org_headers, aid)
    await _index(sid, "Refunds take 14 days to arrive.")
    await go_live(client, aid, org_headers)
    await _index(sid, "Refunds take 30 days to arrive.")

    v1 = await _version(aid, 1)
    async with get_sessionmaker()() as s:
        config, frozen = await manifest.frozen_for(s, v1, AssistantConfig.model_validate(v1.config))
    config.rag.retrieval.min_score = 0.0

    async def search(knowledge: frozenset[uuid.UUID] | None) -> str:
        spec = build_runtime_spec(config, assistant_id=uuid.UUID(aid), knowledge=knowledge)
        (tool,) = [t for t in spec.caps_tools if t.name == "kb_search"]
        result = await tool.handler({"query": "how long do refunds take"})
        return str(result["content"][0]["text"])

    assert "14 days" in await search(frozen.documents)
    assert "30 days" in await search(None), "without a version: the current copy"
