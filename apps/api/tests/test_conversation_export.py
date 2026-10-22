"""Exporting a conversation (Phase 10.5).

- **A file to save**: Markdown or JSON, as an attachment, with the
  messages and their sources.
- **As the reader sees it in Chat**: an employee's export has no tool
  inputs or outputs; a builder's or admin's JSON carries them, as the trace
  does. Sources the reader may not read stay out.
- **Who**: whoever may open the conversation: its starter, or an admin
  with a recorded reason. Recorded as `conversation.export`, without the
  content.
"""

from __future__ import annotations

import json
import uuid
from urllib.parse import quote

import pytest
from app.db.session import get_sessionmaker
from app.models.audit_log import AuditLog
from app.models.conversation import Conversation, Message, MessageRole
from app.models.rag import DataSource, DataSourceStatus, DataSourceType
from app.services import conversation_reads
from httpx import AsyncClient
from sqlalchemy import select, update

from test_audiences import Org, _org  # type: ignore[import-not-found]
from test_history_feedback import _chat, _live  # type: ignore[import-not-found]

pytestmark = pytest.mark.anyio

API = "/api/v1"
SECRET = "ROW 42: salary 99000"


@pytest.fixture
async def org(client: AsyncClient) -> Org:
    return await _org(client)


async def _with_tool_and_source(aid: str, cid: str) -> None:
    """The answer used a tool (whose output is a builder's to see) and
    cited a source everyone may read, plus one only a group may."""
    async with get_sessionmaker()() as s:
        conv = await s.get(Conversation, uuid.UUID(cid))
        assert conv is not None
        sources = []
        for name, everyone in (("Wi-Fi guide", True), ("Payroll notes", False)):
            src = DataSource(
                assistant_id=uuid.UUID(aid),
                org_id=conv.org_id,
                type=DataSourceType.text,
                name=name,
                status=DataSourceStatus.ready,
                everyone=everyone,
                group_ids=[],
            )
            s.add(src)
            sources.append(src)
        await s.flush()
        blocks = [
            {
                "type": "tool_call",
                "id": "t1",
                "name": "mcp__caps__sql_query",
                "input": {"sql": "SELECT * FROM pay"},
                "output": SECRET,
                "status": "ok",
            },
            {
                "type": "citation",
                "marker": 1,
                "chunk_id": "c1",
                "document_id": "d1",
                "data_source_id": str(sources[0].id),
                "title": "Wi-Fi guide",
                "href": "/docs/wifi",
                "snippet": "The guest network is Acme-Guest.",
            },
            {
                "type": "citation",
                "marker": 2,
                "chunk_id": "c2",
                "document_id": "d2",
                "data_source_id": str(sources[1].id),
                "title": "Payroll notes",
                "snippet": "Restricted.",
            },
        ]
        await s.execute(
            update(Message)
            .where(Message.conversation_id == conv.id, Message.role == MessageRole.assistant)
            .values(blocks=blocks)
        )
        await s.commit()


async def test_the_starter_exports_markdown_with_its_sources(client: AsyncClient, org: Org) -> None:
    aid = await _live(client, org)
    cid, _ = await _chat(client, org, "emp1", aid, "Where is the Wi-Fi password?")
    await client.patch(
        f"{API}/conversations/{cid}", json={"title": "Wi-Fi help"}, headers=org.h("emp1")
    )
    await _with_tool_and_source(aid, cid)

    r = await client.get(f"{API}/conversations/{cid}/export", headers=org.h("emp1"))
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/markdown")
    disposition = r.headers["content-disposition"]
    assert disposition.startswith("attachment;") and 'filename="wi-fi-help-' in disposition
    assert disposition.endswith('.md"')
    text = r.text
    assert text.startswith("# Wi-Fi help")
    assert "**You**" in text and "Where is the Wi-Fi password?" in text
    assert "Sources:" in text and "[1] Wi-Fi guide (/docs/wifi)" in text
    assert "_Used: Looked up data_" in text
    # Nothing a tool returned, and no source they may not read.
    assert SECRET not in text and "SELECT" not in text
    assert "Payroll notes" not in text

    async with get_sessionmaker()() as s:
        row = await s.scalar(select(AuditLog).where(AuditLog.action == "conversation.export"))
    assert row is not None and row.target_id == cid
    assert row.meta == {"format": "md", "messages": 2}


async def test_json_is_shaped_for_an_employee_and_complete_for_an_admin(
    client: AsyncClient, org: Org
) -> None:
    aid = await _live(client, org)
    cid, _ = await _chat(client, org, "emp1", aid, "Where is the Wi-Fi password?")
    await _with_tool_and_source(aid, cid)
    url = f"{API}/conversations/{cid}/export"

    mine = await client.get(url, params={"format": "json"}, headers=org.h("emp1"))
    assert mine.status_code == 200, mine.text
    assert mine.headers["content-type"].startswith("application/json")
    assert mine.headers["content-disposition"].endswith('.json"')
    data = json.loads(mine.text)
    assert data["conversation"]["id"] == cid
    roles = [m["role"] for m in data["messages"]]
    assert roles == ["user", "assistant"]
    answer = data["messages"][1]
    assert [c["title"] for c in answer["citations"]] == ["Wi-Fi guide"]
    assert answer["tools"] == [{"name": "Looked up data", "status": "ok"}]
    assert SECRET not in mine.text

    # An admin, with a recorded reason, gets the tool's details too.
    refused = await client.get(url, params={"format": "json"}, headers=org.h("owner"))
    assert refused.status_code != 200
    theirs = await client.get(
        url,
        params={"format": "json"},
        headers={**org.h("owner"), conversation_reads.HEADER: quote("Ticket 7")},
    )
    assert theirs.status_code == 200, theirs.text
    tools = json.loads(theirs.text)["messages"][1]["tools"]
    assert tools[0]["output"] == SECRET and tools[0]["label"] == "Looked up data"
    async with get_sessionmaker()() as s:
        read = await s.scalar(select(AuditLog).where(AuditLog.action == conversation_reads.ACTION))
    assert read is not None


async def test_only_whoever_may_open_it_exports_it(client: AsyncClient, org: Org) -> None:
    aid = await _live(client, org)
    cid, _ = await _chat(client, org, "emp1", aid, "hello")
    url = f"{API}/conversations/{cid}/export"
    for who in ("emp2", "builder"):
        r = await client.get(url, headers=org.h(who))
        assert r.status_code in (403, 404), who
    bad = await client.get(url, params={"format": "pdf"}, headers=org.h("emp1"))
    assert bad.status_code == 422
    async with get_sessionmaker()() as s:
        none = await s.scalar(select(AuditLog).where(AuditLog.action == "conversation.export"))
    assert none is None
