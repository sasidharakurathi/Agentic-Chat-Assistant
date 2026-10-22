"""What erasing one person reaches, and what "What was erased" says
(Phase 10.5).

Erasing a person's data (8.10) deletes their conversations through the one
purge path. These tests pin down every place a conversation's content
lives besides its messages: run traces and tool calls, the rolling
summary, share links, feedback, review decisions (10.3), memory notes
(the conversation's and the person's own), and that each is counted in
the erasure's record. Retention erases quiet conversations the same way,
but leaves a person's own memory notes: they aren't tied to a
conversation.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.db.session import get_sessionmaker
from app.models.conversation import (
    Conversation,
    ConversationShare,
    Message,
    MessageRole,
    Run,
    ToolCall,
)
from app.models.evals import EvalCase
from app.models.feedback import MessageFeedback
from app.models.memory import MemoryFile
from app.models.retention import Erasure
from app.models.review import ReviewItem
from app.services import retention
from httpx import AsyncClient
from sqlalchemy import func, select, update

from test_audiences import Org, _org  # type: ignore[import-not-found]
from test_history_feedback import _chat, _live, _rate  # type: ignore[import-not-found]

pytestmark = pytest.mark.anyio

API = "/api/v1"


@pytest.fixture
async def org(client: AsyncClient) -> Org:
    return await _org(client)


async def _everything(org: Org, aid: str, cid: str, who: str) -> None:
    """A summary, a share link, a tool call, and memory notes: the
    conversation's and the person's own."""
    user = org.people[who].user_id
    async with get_sessionmaker()() as s:
        conv = await s.get(Conversation, uuid.UUID(cid))
        assert conv is not None
        conv.summary = "They asked about the Wi-Fi password twice."
        answer = await s.scalar(
            select(Message).where(
                Message.conversation_id == conv.id, Message.role == MessageRole.assistant
            )
        )
        assert answer is not None
        s.add(
            ConversationShare(
                org_id=conv.org_id,
                conversation_id=conv.id,
                created_by=uuid.UUID(user),
                token_hash=hashlib.sha256(cid.encode()).hexdigest(),
            )
        )
        s.add(
            ToolCall(
                message_id=answer.id,
                conversation_id=conv.id,
                org_id=conv.org_id,
                tool_name="mcp__caps__kb_search",
                server="caps",
                input={"query": "wifi"},
                output="Acme-Guest",
            )
        )
        for key in (f"conv:{cid}", f"user:{user}"):
            s.add(
                MemoryFile(
                    org_id=conv.org_id,
                    assistant_id=uuid.UUID(aid),
                    owner_key=key,
                    path="/memories/notes.md",
                    content="prefers email",
                )
            )
        await s.commit()


async def _count(model: type, *where: object) -> int:
    async with get_sessionmaker()() as s:
        return int(await s.scalar(select(func.count()).select_from(model).where(*where)) or 0)


async def test_erasing_a_person_reaches_everything_and_says_what_went(
    client: AsyncClient, org: Org
) -> None:
    aid = await _live(client, org)
    cid, mid = await _chat(client, org, "emp1", aid, "Where is the Wi-Fi password?")
    await _rate(client, org, "emp1", cid, mid, rating="down", comment="It didn't know.")
    await _everything(org, aid, cid, "emp1")
    # The builders turned it into a test case (10.3).
    suite = await client.post(
        f"{API}/assistants/{aid}/eval-suites", json={"name": "From Chat"}, headers=org.h("builder")
    )
    made = await client.post(
        f"{API}/assistants/{aid}/review-queue/{mid}:to-case",
        json={"suite_id": suite.json()["id"]},
        headers=org.h("builder"),
    )
    assert made.status_code == 200, made.text
    traces = await _count(Run, Run.conversation_id == uuid.UUID(cid))
    assert traces >= 1
    other, _ = await _chat(client, org, "emp2", aid, "Printer?")

    user = org.people["emp1"].user_id
    r = await client.post(
        f"{API}/orgs/{org.id}/members/{user}:erase",
        json={"confirm_email": "emp1@example.com"},
        headers=org.h("owner"),
    )
    assert r.status_code == 200, r.text
    # The response keeps its shape (8.10); the detail is in the record.
    assert set(r.json()) == {"conversations", "kept", "memories", "comments"}
    assert r.json()["memories"] == 1

    gone = uuid.UUID(cid)
    for model, where in (
        (Conversation, Conversation.id == gone),
        (Message, Message.conversation_id == gone),
        (Run, Run.conversation_id == gone),
        (ToolCall, ToolCall.conversation_id == gone),
        (ConversationShare, ConversationShare.conversation_id == gone),
        (MessageFeedback, MessageFeedback.conversation_id == gone),
        (ReviewItem, ReviewItem.message_id == uuid.UUID(mid)),
        (MemoryFile, MemoryFile.owner_key == f"conv:{cid}"),
        (MemoryFile, MemoryFile.owner_key == f"user:{user}"),
    ):
        assert await _count(model, where) == 0, model.__name__
    # Someone else's conversation stays.
    assert await _count(Conversation, Conversation.id == uuid.UUID(other)) == 1
    # The test case the builders wrote stays: theirs now, without a name.
    assert await _count(EvalCase) == 1

    async with get_sessionmaker()() as s:
        record = await s.scalar(select(Erasure).where(Erasure.kind == "person"))
    assert record is not None
    details = record.details
    assert details["messages"] == 2
    assert details["traces"] == traces
    assert details["tool_calls"] == 1
    assert details["summaries"] == 1
    assert details["share_links"] == 1
    assert details["feedback"] == 1
    assert details["review_items"] == 1
    assert details["conversation_memories"] == 1

    listed = await client.get(f"{API}/orgs/{org.id}/erasures", headers=org.h("owner"))
    assert listed.status_code == 200, listed.text
    (row,) = listed.json()
    assert row["details"]["summaries"] == 1 and row["details"]["traces"] == traces
    assert "Wi-Fi" not in listed.text


async def test_retention_counts_what_went_and_leaves_a_persons_own_notes(
    client: AsyncClient, org: Org
) -> None:
    aid = await _live(client, org)
    cid, _ = await _chat(client, org, "emp1", aid, "hello")
    await _everything(org, aid, cid, "emp1")
    then = datetime.now(UTC) - timedelta(days=400)
    async with get_sessionmaker()() as s:
        await s.execute(
            update(Conversation)
            .where(Conversation.id == uuid.UUID(cid))
            .values(last_message_at=then, created_at=then)
        )
        await s.commit()
        assert await retention.apply_retention(s, uuid.UUID(org.id)) == 1
    user = org.people["emp1"].user_id
    assert await _count(Conversation, Conversation.id == uuid.UUID(cid)) == 0
    assert await _count(MemoryFile, MemoryFile.owner_key == f"conv:{cid}") == 0
    # Not time-based: a person's own notes go only when they are erased.
    assert await _count(MemoryFile, MemoryFile.owner_key == f"user:{user}") == 1
    async with get_sessionmaker()() as s:
        record = await s.scalar(select(Erasure).where(Erasure.kind == "retention"))
    assert record is not None
    assert record.details["summaries"] == 1 and record.details["share_links"] == 1
    assert record.details["messages"] == 2 and record.details["tool_calls"] == 1
