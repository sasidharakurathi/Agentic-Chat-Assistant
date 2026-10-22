"""History and feedback (Phase 8.8).

- **Removing** a conversation takes it out of your lists and search, and
  says so honestly: it is kept, admins can still open it (the read is
  flagged as one its owner removed), and you can put it back. Erasing comes
  with the retention rules (8.10).
- **Search** finds your own conversations across assistants, by title or
  by anything said in them.
- **Feedback**: a thumbs on each answer, with an optional comment, from the
  conversation's starter. "Did this solve it?" once per conversation, kept
  without a name.
- **The builders** see counts and each comment with its question and
  answer, never who asked; an admin also gets the conversation.
"""

from __future__ import annotations

import uuid
from typing import Any
from urllib.parse import quote

import pytest
from app.db.session import get_sessionmaker
from app.models.audit_log import AuditLog
from app.models.conversation import Conversation
from app.models.feedback import ConversationOutcome
from app.services import conversation_reads, privacy
from httpx import AsyncClient
from sqlalchemy import delete, select
from tests.versions import go_live

from test_audiences import Org, _org  # type: ignore[import-not-found]

pytestmark = pytest.mark.anyio

API = "/api/v1"


@pytest.fixture
async def org(client: AsyncClient) -> Org:
    return await _org(client)


async def _live(client: AsyncClient, org: Org, name: str = "Helpdesk") -> str:
    h = org.h("builder")
    aid = (await client.post(f"{API}/assistants", json={"name": name}, headers=h)).json()["id"]
    await go_live(client, aid, h, approver=org.h("owner"))
    return str(aid)


async def _chat(client: AsyncClient, org: Org, who: str, aid: str, text: str, **body: Any):
    """A conversation with one question answered: its id, and the answer's."""
    r = await client.post(f"{API}/assistants/{aid}/conversations", json=body, headers=org.h(who))
    assert r.status_code == 201, r.text
    cid = str(r.json()["id"])
    said = await client.post(
        f"{API}/conversations/{cid}/messages", json={"text": text}, headers=org.h(who)
    )
    assert said.status_code == 200, said.text
    detail = (await client.get(f"{API}/conversations/{cid}", headers=org.h(who))).json()
    answer = next(m for m in detail["messages"] if m["role"] == "assistant")
    return cid, str(answer["id"])


async def _mine(client: AsyncClient, org: Org, who: str, **params: Any) -> list[str]:
    r = await client.get(f"{API}/chat/conversations", params=params, headers=org.h(who))
    assert r.status_code == 200, r.text
    return [c["id"] for c in r.json()["items"]]


# ── removing, honestly ───────────────────────────────────────


async def test_removing_hides_it_from_you_keeps_it_and_can_be_undone(
    client: AsyncClient, org: Org
) -> None:
    aid = await _live(client, org)
    cid, _ = await _chat(client, org, "emp1", aid, "my laptop won't start")
    gone = await client.delete(f"{API}/conversations/{cid}", headers=org.h("emp1"))
    assert gone.status_code == 200
    assert cid not in await _mine(client, org, "emp1")
    assert await _mine(client, org, "emp1", removed="true") == [cid]
    # Search doesn't find it either.
    assert await _mine(client, org, "emp1", q="laptop") == []

    # Kept: an admin can still open it, and the read says its owner removed it.
    read = await client.get(
        f"{API}/conversations/{cid}",
        headers={**org.h("owner"), conversation_reads.HEADER: quote("Ticket 12")},
    )
    assert read.status_code == 200
    async with get_sessionmaker()() as s:
        row = await s.scalar(select(AuditLog).where(AuditLog.action == conversation_reads.ACTION))
    assert row is not None and row.meta["hidden_by_owner"] is True

    # Removed, it can't be continued; put back, it can.
    refused = await client.post(
        f"{API}/conversations/{cid}/messages", json={"text": "hi"}, headers=org.h("emp1")
    )
    assert refused.status_code == 409
    back = await client.post(f"{API}/conversations/{cid}:restore", headers=org.h("emp1"))
    assert back.status_code == 200 and back.json()["status"] == "active"
    assert cid in await _mine(client, org, "emp1")
    again = await client.post(
        f"{API}/conversations/{cid}/messages", json={"text": "hi"}, headers=org.h("emp1")
    )
    assert again.status_code == 200
    async with get_sessionmaker()() as s:
        restored = await s.scalar(select(AuditLog).where(AuditLog.action == "conversation.restore"))
    assert restored is not None


async def test_only_its_starter_puts_it_back(client: AsyncClient, org: Org) -> None:
    aid = await _live(client, org)
    cid, _ = await _chat(client, org, "emp1", aid, "hello")
    await client.delete(f"{API}/conversations/{cid}", headers=org.h("emp1"))
    for who in ("emp2", "owner"):
        r = await client.post(f"{API}/conversations/{cid}:restore", headers=org.h(who))
        assert r.status_code in (403, 404), who


# ── search across assistants ─────────────────────────────────


async def test_search_finds_your_conversations_by_title_or_words(
    client: AsyncClient, org: Org
) -> None:
    helpdesk = await _live(client, org)
    hr = await _live(client, org, name="HR")
    vpn, _ = await _chat(client, org, "emp1", helpdesk, "My VPN keeps dropping")
    leave, _ = await _chat(client, org, "emp1", hr, "How much leave do I have?")
    await client.patch(
        f"{API}/conversations/{leave}", json={"title": "Holiday plans"}, headers=org.h("emp1")
    )
    # "VPN" only in what was said, not the title: search reads the messages.
    await client.patch(
        f"{API}/conversations/{vpn}", json={"title": "Network trouble"}, headers=org.h("emp1")
    )
    theirs, _ = await _chat(client, org, "emp2", helpdesk, "VPN again")

    assert await _mine(client, org, "emp1", q="vpn") == [vpn]
    assert await _mine(client, org, "emp1", q="holiday") == [leave]
    assert set(await _mine(client, org, "emp1")) == {vpn, leave}
    # Someone else's never shows, and a wildcard is just a character.
    assert theirs not in await _mine(client, org, "emp1", q="VPN")
    assert await _mine(client, org, "emp1", q="%") == []
    assert await _mine(client, org, "emp1", q="_") == []


# ── feedback ─────────────────────────────────────────────────


async def _rate(client: AsyncClient, org: Org, who: str, cid: str, mid: str, **body: Any):
    return await client.put(
        f"{API}/conversations/{cid}/messages/{mid}/feedback", json=body, headers=org.h(who)
    )


async def test_a_thumbs_with_a_comment_can_change_and_be_taken_back(
    client: AsyncClient, org: Org
) -> None:
    aid = await _live(client, org)
    cid, mid = await _chat(client, org, "emp1", aid, "How do I reset my password?")
    up = await _rate(client, org, "emp1", cid, mid, rating="up")
    assert up.json() == {"rating": "up", "comment": ""}
    down = await _rate(
        client, org, "emp1", cid, mid, rating="down", comment="  It linked an old page.  "
    )
    assert down.json() == {"rating": "down", "comment": "It linked an old page."}
    detail = (await client.get(f"{API}/conversations/{cid}", headers=org.h("emp1"))).json()
    assert detail["feedback"] == {mid: {"rating": "down", "comment": "It linked an old page."}}

    back = await _rate(client, org, "emp1", cid, mid, rating=None)
    assert back.json() == {"rating": None, "comment": ""}
    detail = (await client.get(f"{API}/conversations/{cid}", headers=org.h("emp1"))).json()
    assert detail["feedback"] == {}


async def test_only_the_starter_rates_and_only_answers(client: AsyncClient, org: Org) -> None:
    aid = await _live(client, org)
    cid, mid = await _chat(client, org, "emp1", aid, "hello")
    for who in ("emp2", "owner"):
        assert (await _rate(client, org, who, cid, mid, rating="up")).status_code in (403, 404)
    detail = (await client.get(f"{API}/conversations/{cid}", headers=org.h("emp1"))).json()
    question = next(m for m in detail["messages"] if m["role"] == "user")["id"]
    wrong = await _rate(client, org, "emp1", cid, question, rating="up")
    assert (wrong.status_code, wrong.json()["error"]["code"]) == (404, "message_not_found")
    _other, other_mid = await _chat(client, org, "emp1", aid, "another")
    borrowed = await _rate(client, org, "emp1", cid, other_mid, rating="up")
    assert borrowed.status_code == 404
    long = await _rate(client, org, "emp1", cid, mid, rating="down", comment="x" * 1001)
    assert long.status_code == 422


async def test_did_this_solve_it_is_one_answer_per_conversation_and_outlives_it(
    client: AsyncClient, org: Org
) -> None:
    aid = await _live(client, org)
    empty = (
        await client.post(f"{API}/assistants/{aid}/conversations", json={}, headers=org.h("emp1"))
    ).json()["id"]
    early = await client.put(
        f"{API}/conversations/{empty}/outcome", json={"solved": True}, headers=org.h("emp1")
    )
    assert (early.status_code, early.json()["error"]["code"]) == (400, "nothing_answered")

    cid, _ = await _chat(client, org, "emp1", aid, "VPN?")
    url = f"{API}/conversations/{cid}/outcome"
    assert (await client.put(url, json={"solved": True}, headers=org.h("emp1"))).json() == {
        "solved": True
    }
    assert (await client.put(url, json={"solved": False}, headers=org.h("emp1"))).json() == {
        "solved": False
    }
    detail = (await client.get(f"{API}/conversations/{cid}", headers=org.h("emp1"))).json()
    assert detail["solved"] is False

    # Erased later (8.10): the outcome, with no name on it, still counts.
    async with get_sessionmaker()() as s:
        await s.execute(delete(Conversation).where(Conversation.id == uuid.UUID(cid)))
        await s.commit()
        outcomes = list(await s.scalars(select(ConversationOutcome)))
    assert len(outcomes) == 1 and outcomes[0].solved is False
    assert not hasattr(outcomes[0], "user_id")


# ── for the builders ─────────────────────────────────────────


async def test_builders_see_counts_and_comments_without_who_asked(
    client: AsyncClient, org: Org
) -> None:
    aid = await _live(client, org)
    cid, mid = await _chat(client, org, "emp1", aid, "Where is the Wi-Fi password?")
    await _rate(client, org, "emp1", cid, mid, rating="down", comment="It didn't know.")
    await client.put(
        f"{API}/conversations/{cid}/outcome", json={"solved": False}, headers=org.h("emp1")
    )
    cid2, mid2 = await _chat(client, org, "emp2", aid, "VPN steps?")
    await _rate(client, org, "emp2", cid2, mid2, rating="up")
    # A preview is the builder's own testing: not counted.
    pcid, pmid = await _chat(client, org, "builder", aid, "test", mode="preview")
    await _rate(client, org, "builder", pcid, pmid, rating="down", comment="draft test")

    r = await client.get(f"{API}/assistants/{aid}/feedback", headers=org.h("builder"))
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["versions"] == [{"version_number": 1, "up": 1, "down": 1}]
    assert (got["solved_yes"], got["solved_no"], got["conversations"]) == (0, 1, 2)
    (comment,) = got["comments"]
    assert comment["comment"] == "It didn't know." and comment["rating"] == "down"
    assert comment["question"] == "Where is the Wi-Fi password?" and comment["answer"]
    assert comment["conversation_id"] is None
    assert "emp1" not in r.text and "user_id" not in r.text

    admin = (await client.get(f"{API}/assistants/{aid}/feedback", headers=org.h("owner"))).json()
    assert admin["comments"][0]["conversation_id"] == cid
    refused = await client.get(f"{API}/assistants/{aid}/feedback", headers=org.h("emp1"))
    assert refused.status_code == 403
    other = await client.get(f"{API}/assistants/{aid}/feedback", headers=org.h("builder2"))
    assert other.status_code == 403


def test_the_notice_says_who_sees_feedback() -> None:
    assert "without your name" in privacy.DEFAULT_BODY
    assert "Feedback you send" in privacy.DEFAULT_BODY
