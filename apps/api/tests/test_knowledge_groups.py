"""Knowledge groups, revocation, the ingest scan and share links (Phase 9.4).

- A source is everyone's or readable by groups; retrieval, citations and
  who is offered the assistant follow it (plan D6).
- Losing access bumps the organisation's access epoch: a conversation on
  restricted knowledge starts fresh, and earlier citations hide.
- A source open to everyone that holds credentials or identity numbers is
  held back until its builder chooses groups or confirms everyone.
- A shared conversation opens read-only for a member who may use the
  assistant and read what it cites, while its sharer is still a member.
"""

from __future__ import annotations

import contextlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from app.db.session import get_sessionmaker
from app.models.audit_log import AuditLog
from app.models.conversation import Conversation, ConversationMode, Message, MessageRole
from app.models.organization import Organization
from app.models.rag import DataSource, DataSourceStatus, Document
from app.rag import ingest, sensitivity
from app.schemas.assistant_config import AssistantConfig
from app.services import chat as chat_svc
from app.services import knowledge_access
from httpx import AsyncClient
from sqlalchemy import select
from tests.versions import go_live

from test_orgs import Actor, _register  # type: ignore[import-not-found]

pytestmark = pytest.mark.anyio

API = "/api/v1"
KEY = "sk-ant-" + "x" * 30  # shaped like a key, not one
PANS = "ABCDE1234F, PQRST5678K and LMNOP9012Z"


# ── the scan ─────────────────────────────────────────────────


def test_a_credential_holds_a_source_back() -> None:
    report = sensitivity.scan(f"Our deploy key is {KEY}.", "c1")
    assert report.flagged and "credential" in report.reason
    assert report.samples == [{"kind": "secret", "value": "sk-a…"}], "masked"
    assert report.checksum == "c1"


def test_a_few_identity_numbers_hold_it_back_and_phones_never_do() -> None:
    assert sensitivity.scan(f"PAN list: {PANS}").flagged
    assert not sensitivity.scan("PAN: ABCDE1234F").flagged, "one could be a product code"
    phones = sensitivity.scan("IT desk 9876543210, 9876543211, 9876543212, 9876543213")
    assert not phones.flagged and phones.counts["phone"] == 4


def test_masking_keeps_only_the_end() -> None:
    assert sensitivity.mask("pan", "ABCDE1234F") == "••••••234F"
    assert sensitivity.mask("aadhaar", "2345 6789 0123") == "••••••••0123"


# ── people, an assistant, a source ───────────────────────────


async def _org(client: AsyncClient) -> tuple[str, dict[str, Actor]]:
    owner = await _register(client, "owner@example.com")
    org = (await client.post(f"{API}/orgs", json={"name": "Acme"}, headers=owner.headers)).json()
    people = {"owner": owner}
    for name in ("pat", "sam"):
        actor = await _register(client, f"{name}@example.com")
        inv = await client.post(
            f"{API}/orgs/{org['id']}/invites",
            json={"email": f"{name}@example.com"},
            headers=owner.headers,
        )
        token = inv.json()["accept_url"].rsplit("/", 1)[-1]
        assert (
            await client.post(f"{API}/invites/{token}/accept", headers=actor.headers)
        ).is_success
        people[name] = actor
    return org["id"], people


def _h(org_id: str, actor: Actor) -> dict[str, str]:
    return {**actor.headers, "X-Org-Id": org_id}


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[uuid.UUID]:
    """Ingestion jobs asked for, instead of run."""
    calls: list[uuid.UUID] = []

    async def enqueue(source_id: uuid.UUID) -> bool:
        calls.append(source_id)
        return True

    monkeypatch.setattr("app.services.data_sources.enqueue_ingest", enqueue)
    return calls


async def _source(client: AsyncClient, h: dict[str, str], aid: str, text: str) -> str:
    r = await client.post(
        f"{API}/assistants/{aid}/data-sources",
        json={"type": "text", "name": "HR handbook", "text": text},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def _group(client: AsyncClient, org_id: str, h: dict[str, str], name: str) -> str:
    r = await client.post(f"{API}/orgs/{org_id}/groups", json={"name": name}, headers=h)
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def _join(client: AsyncClient, org_id: str, h: dict[str, str], gid: str, who: Actor) -> None:
    r = await client.post(
        f"{API}/orgs/{org_id}/groups/{gid}/members", json={"user_id": who.user_id}, headers=h
    )
    assert r.status_code in (200, 201), r.text


async def _leave(client: AsyncClient, org_id: str, h: dict[str, str], gid: str, who: Actor) -> None:
    r = await client.delete(f"{API}/orgs/{org_id}/groups/{gid}/members/{who.user_id}", headers=h)
    assert r.status_code == 200, r.text


async def _live_assistant(
    client: AsyncClient, h: dict[str, str], sid_text: str = "Leave policy."
) -> tuple[str, str]:
    a = (await client.post(f"{API}/assistants", json={"name": "HR desk"}, headers=h)).json()
    sid = await _source(client, h, a["id"], sid_text)
    cfg = a["draft_config"]
    cfg["rag"]["enabled"] = True
    cfg["rag"]["source_ids"] = [sid]
    r = await client.put(f"{API}/assistants/{a['id']}/draft-config", json=cfg, headers=h)
    assert r.status_code == 200, r.text
    return str(a["id"]), sid


async def _restrict(
    client: AsyncClient, h: dict[str, str], aid: str, sid: str, groups: list[str]
) -> Any:
    return await client.put(
        f"{API}/assistants/{aid}/data-sources/{sid}/access",
        json={"everyone": False, "group_ids": groups},
        headers=h,
    )


async def _epoch(org_id: str) -> int:
    async with get_sessionmaker()() as s:
        org = await s.get(Organization, uuid.UUID(org_id))
        assert org is not None
        return org.access_epoch


async def _offered(client: AsyncClient, h: dict[str, str]) -> list[str]:
    r = await client.get(f"{API}/chat/assistants", headers=h)
    assert r.status_code == 200, r.text
    return [a["name"] for a in r.json()]


# ── who may read a source ────────────────────────────────────


async def test_access_is_checked_audited_and_bumps_the_epoch(
    client: AsyncClient, queued: list[uuid.UUID]
) -> None:
    org_id, people = await _org(client)
    h = _h(org_id, people["owner"])
    aid, sid = await _live_assistant(client, h)
    hr = await _group(client, org_id, h, "HR")

    bad = await _restrict(client, h, aid, sid, [str(uuid.uuid4())])
    assert (bad.status_code, bad.json()["error"]["code"]) == (400, "unknown_group")

    before = await _epoch(org_id)
    r = await _restrict(client, h, aid, sid, [hr])
    assert r.status_code == 200, r.text
    assert (r.json()["everyone"], r.json()["group_ids"]) == (False, [hr])
    assert await _epoch(org_id) == before + 1
    async with get_sessionmaker()() as s:
        row = await s.scalar(select(AuditLog).where(AuditLog.action == "data_source.access"))
    assert row is not None
    assert row.meta["from"] == {"everyone": True, "group_ids": []}
    assert row.meta["to"] == {"everyone": False, "group_ids": [hr]}


async def test_the_assistant_is_offered_only_to_readers(
    client: AsyncClient, queued: list[uuid.UUID]
) -> None:
    org_id, people = await _org(client)
    h = _h(org_id, people["owner"])
    aid, sid = await _live_assistant(client, h)
    hr = await _group(client, org_id, h, "HR")
    assert (await _restrict(client, h, aid, sid, [hr])).status_code == 200
    await go_live(client, aid, h)
    pat = _h(org_id, people["pat"])

    assert "HR desk" not in await _offered(client, pat), "can't read its knowledge"
    await _join(client, org_id, h, hr, people["pat"])
    assert "HR desk" in await _offered(client, pat)
    assert "HR desk" in await _offered(client, h), "the owner edits it"


async def test_a_turn_reads_only_what_the_asker_may(
    client: AsyncClient, queued: list[uuid.UUID]
) -> None:
    org_id, people = await _org(client)
    h = _h(org_id, people["owner"])
    aid, sid = await _live_assistant(client, h)
    other = await _source(client, h, aid, "Office hours.")
    hr = await _group(client, org_id, h, "HR")
    assert (await _restrict(client, h, aid, sid, [hr])).status_code == 200
    config = AssistantConfig.model_validate(
        {"rag": {"enabled": True, "source_ids": [sid, other]}, "memory": {"memory_tool": True}}
    )

    async def access(who: Actor) -> knowledge_access.TurnAccess:
        async with get_sessionmaker()() as s:
            return await knowledge_access.for_turn(
                s,
                org_id=uuid.UUID(org_id),
                assistant_id=uuid.UUID(aid),
                asker_id=uuid.UUID(who.user_id),
                config=config,
            )

    pat = await access(people["pat"])
    assert pat.config.rag.source_ids == [other], "only what pat may read"
    assert pat.restricted and not pat.config.memory.memory_tool, "no memory on restricted"
    owner = await access(people["owner"])
    assert owner.readable is None and sorted(owner.config.rag.source_ids) == sorted([sid, other])

    config.rag.source_ids = [sid]
    alone = await access(people["pat"])
    assert alone.config.rag.enabled is False, "never an empty list, which means every source"


async def test_losing_access_starts_the_conversation_afresh(
    client: AsyncClient, queued: list[uuid.UUID]
) -> None:
    org_id, people = await _org(client)
    h = _h(org_id, people["owner"])
    aid, sid = await _live_assistant(client, h)
    hr = await _group(client, org_id, h, "HR")
    await _join(client, org_id, h, hr, people["pat"])
    assert (await _restrict(client, h, aid, sid, [hr])).status_code == 200
    epoch = await _epoch(org_id)

    async with get_sessionmaker()() as s:
        conv = Conversation(
            assistant_id=uuid.UUID(aid),
            org_id=uuid.UUID(org_id),
            created_by=uuid.UUID(people["pat"].user_id),
            mode=ConversationMode.live,
            sdk_session_id="earlier-session",
            citation_state={"markers": {"1": "chunk"}, "next": 2},
            access_epoch=epoch,
        )
        s.add(conv)
        await s.flush()
        s.add(
            Message(
                conversation_id=conv.id,
                org_id=uuid.UUID(org_id),
                role=MessageRole.assistant,
                content="You have 12 days [1].",
                blocks=[
                    {"type": "citation", "marker": 1, "data_source_id": sid, "title": "HR"},
                    {"type": "tool_call", "name": "kb_search", "output": "Pat: 12 days"},
                ],
            )
        )
        await s.commit()
        cid = conv.id

    async def for_asker() -> tuple[bool, Conversation]:
        async with get_sessionmaker()() as s:
            conv = await s.get(Conversation, cid)
            assert conv is not None
            cfg = AssistantConfig.model_validate({"rag": {"enabled": True, "source_ids": [sid]}})
            _, fresh = await chat_svc._for_asker(s, conv, cfg)
            return fresh, conv

    fresh, conv = await for_asker()
    assert not fresh and conv.sdk_session_id == "earlier-session", "nothing changed"

    await _leave(client, org_id, h, hr, people["pat"])
    assert await _epoch(org_id) == epoch + 1
    fresh, conv = await for_asker()
    assert fresh
    assert (conv.sdk_session_id, conv.citation_state, conv.access_epoch) == (None, {}, epoch + 1)
    fresh, _ = await for_asker()
    assert not fresh, "once"

    # The earlier answer stays theirs; the citation of what they lost hides.
    shown = await client.get(f"{API}/conversations/{cid}", headers=_h(org_id, people["pat"]))
    assert shown.status_code == 200, shown.text
    (answer,) = shown.json()["messages"]
    assert not [b for b in answer["blocks"] if b.get("type") == "citation"]


async def test_deleting_a_group_never_opens_its_sources(
    client: AsyncClient, queued: list[uuid.UUID]
) -> None:
    org_id, people = await _org(client)
    h = _h(org_id, people["owner"])
    aid, sid = await _live_assistant(client, h)
    hr = await _group(client, org_id, h, "HR")
    assert (await _restrict(client, h, aid, sid, [hr])).status_code == 200
    r = await client.delete(f"{API}/orgs/{org_id}/groups/{hr}", headers=h)
    assert r.status_code == 200, r.text
    assert "1 knowledge source" in r.json()["message"]
    async with get_sessionmaker()() as s:
        source = await s.get(DataSource, uuid.UUID(sid))
        assert source is not None and source.everyone is False
        assert not knowledge_access.readable(source.everyone, source.group_ids, set())


# ── the scan at ingest ───────────────────────────────────────


async def _ingest(sid: str) -> DataSource:
    async with get_sessionmaker()() as s:
        # Past the hold, the stub stops it: recorded, then raised.
        with contextlib.suppress(RuntimeError):
            await ingest.ingest_data_source(s, uuid.UUID(sid))
    async with get_sessionmaker()() as s:
        source = await s.get(DataSource, uuid.UUID(sid))
        assert source is not None
        return source


@pytest.fixture
def past_the_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ingestion stops just after the hold, so nothing is embedded."""

    async def stop(*_a: Any) -> Any:
        raise RuntimeError("past the hold")

    monkeypatch.setattr(ingest, "_rag_settings", stop)


async def test_a_source_with_a_key_waits_then_is_confirmed(
    client: AsyncClient, queued: list[uuid.UUID], past_the_hold: None
) -> None:
    org_id, people = await _org(client)
    h = _h(org_id, people["owner"])
    a = (await client.post(f"{API}/assistants", json={"name": "Ops"}, headers=h)).json()
    sid = await _source(client, h, a["id"], f"Deploy with {KEY}.")

    source = await _ingest(sid)
    assert source.status is DataSourceStatus.held
    scanned = (source.ingest_report or {})["sensitive"]
    assert "credential" in scanned["reason"] and KEY not in str(scanned)
    async with get_sessionmaker()() as s:
        docs = (await s.scalars(select(Document).where(Document.data_source_id == source.id))).all()
    assert docs == [], "nothing indexed"

    early = await client.post(
        f"{API}/assistants/{a['id']}/data-sources/{uuid.uuid4()}:confirm-everyone", headers=h
    )
    assert early.status_code == 404
    r = await client.post(
        f"{API}/assistants/{a['id']}/data-sources/{sid}:confirm-everyone", headers=h
    )
    assert r.status_code == 200, r.text
    assert queued[-1] == uuid.UUID(sid)
    async with get_sessionmaker()() as s:
        row = await s.scalar(
            select(AuditLog).where(AuditLog.action == "data_source.sensitive_confirmed")
        )
    assert row is not None and row.meta["counts"] == {"secret": 1}

    source = await _ingest(sid)
    assert source.error == "past the hold", "indexed this time"
    again = await client.post(
        f"{API}/assistants/{a['id']}/data-sources/{sid}:confirm-everyone", headers=h
    )
    assert (again.status_code, again.json()["error"]["code"]) == (400, "not_held")


async def test_a_restricted_source_is_never_held(
    client: AsyncClient, queued: list[uuid.UUID], past_the_hold: None
) -> None:
    org_id, people = await _org(client)
    h = _h(org_id, people["owner"])
    a = (await client.post(f"{API}/assistants", json={"name": "Ops"}, headers=h)).json()
    sid = await _source(client, h, a["id"], f"Staff PANs: {PANS}.")
    assert (await _ingest(sid)).status is DataSourceStatus.held

    hr = await _group(client, org_id, h, "HR")
    r = await _restrict(client, h, a["id"], sid, [hr])
    assert r.status_code == 200 and r.json()["status"] == "pending", "queued again"
    assert queued[-1] == uuid.UUID(sid)
    assert (await _ingest(sid)).error == "past the hold"


# ── share links ──────────────────────────────────────────────


async def _shared_conversation(
    client: AsyncClient, org_id: str, people: dict[str, Actor]
) -> tuple[str, str, str, str]:
    """A live HR assistant restricted to HR (pat and sam in its audience,
    only pat in HR), and pat's conversation citing it."""
    h = _h(org_id, people["owner"])
    aid, sid = await _live_assistant(client, h)
    hr = await _group(client, org_id, h, "HR")
    await _join(client, org_id, h, hr, people["pat"])
    assert (await _restrict(client, h, aid, sid, [hr])).status_code == 200
    await go_live(client, aid, h)
    async with get_sessionmaker()() as s:
        conv = Conversation(
            assistant_id=uuid.UUID(aid),
            org_id=uuid.UUID(org_id),
            created_by=uuid.UUID(people["pat"].user_id),
            mode=ConversationMode.live,
            title="My leave",
        )
        s.add(conv)
        await s.flush()
        s.add_all(
            [
                Message(
                    conversation_id=conv.id,
                    org_id=uuid.UUID(org_id),
                    role=MessageRole.user,
                    content="How much leave do I have?",
                    blocks=[],
                    created_at=datetime.now(UTC) - timedelta(seconds=5),
                ),
                Message(
                    conversation_id=conv.id,
                    org_id=uuid.UUID(org_id),
                    role=MessageRole.assistant,
                    content="12 days [1].",
                    blocks=[
                        {"type": "citation", "marker": 1, "data_source_id": sid, "title": "HR"},
                        {"type": "tool_call", "name": "sql_query", "output": "pat | 12"},
                    ],
                ),
            ]
        )
        await s.commit()
        return aid, sid, hr, str(conv.id)


async def test_a_share_opens_only_for_readers_and_shows_no_tool_output(
    client: AsyncClient, queued: list[uuid.UUID]
) -> None:
    org_id, people = await _org(client)
    h = _h(org_id, people["owner"])
    _aid, _sid, hr, cid = await _shared_conversation(client, org_id, people)
    pat, sam = _h(org_id, people["pat"]), _h(org_id, people["sam"])

    other = await client.post(f"{API}/conversations/{cid}/shares", headers=sam)
    assert other.status_code in (403, 404), "only the person who started it"
    made = await client.post(f"{API}/conversations/{cid}/shares", headers=pat)
    assert made.status_code == 201, made.text
    token = made.json()["url"].rsplit("/", 1)[-1]

    refused = await client.get(f"{API}/shared/{token}", headers=people["sam"].headers)
    assert (refused.status_code, refused.json()["error"]["code"]) == (403, "share_not_for_you")
    await _join(client, org_id, h, hr, people["sam"])
    opened = await client.get(f"{API}/shared/{token}", headers=people["sam"].headers)
    assert opened.status_code == 200, opened.text
    body = opened.json()
    assert (body["title"], body["assistant_name"]) == ("My leave", "HR desk")
    assert [m["role"] for m in body["messages"]] == ["user", "assistant"]
    assert [b["type"] for b in body["messages"][1]["blocks"]] == ["citation"]
    assert "pat | 12" not in opened.text

    (listed,) = (await client.get(f"{API}/conversations/{cid}/shares", headers=pat)).json()
    assert listed["open_count"] == 1
    async with get_sessionmaker()() as s:
        row = await s.scalar(select(AuditLog).where(AuditLog.action == "conversation.share_opened"))
    assert row is not None and row.actor_user_id == uuid.UUID(people["sam"].user_id)

    stranger = await _register(client, "stranger@example.com")
    gone = await client.get(f"{API}/shared/{token}", headers=stranger.headers)
    assert (gone.status_code, gone.json()["error"]["code"]) == (404, "share_gone")
    assert (await client.get(f"{API}/shared/nonsense", headers=sam)).status_code == 404

    revoked = await client.delete(f"{API}/conversations/{cid}/shares/{listed['id']}", headers=pat)
    assert revoked.status_code == 200 and revoked.json()["revoked_at"]
    after = await client.get(f"{API}/shared/{token}", headers=people["sam"].headers)
    assert after.status_code == 404


async def test_a_leavers_links_stop_and_previews_are_never_shared(
    client: AsyncClient, queued: list[uuid.UUID]
) -> None:
    org_id, people = await _org(client)
    h = _h(org_id, people["owner"])
    aid, _sid, hr, cid = await _shared_conversation(client, org_id, people)
    await _join(client, org_id, h, hr, people["sam"])
    pat = _h(org_id, people["pat"])
    token = (
        (await client.post(f"{API}/conversations/{cid}/shares", headers=pat))
        .json()["url"]
        .rsplit("/", 1)[-1]
    )
    assert (await client.get(f"{API}/shared/{token}", headers=people["sam"].headers)).is_success
    r = await client.post(
        f"{API}/orgs/{org_id}/members/{people['pat'].user_id}:deactivate", json={}, headers=h
    )
    assert r.status_code == 200, r.text
    stopped = await client.get(f"{API}/shared/{token}", headers=people["sam"].headers)
    assert (stopped.status_code, stopped.json()["error"]["code"]) == (404, "share_gone")

    preview = await client.post(
        f"{API}/assistants/{aid}/conversations", json={"mode": "preview"}, headers=h
    )
    assert preview.status_code == 201, preview.text
    no = await client.post(f"{API}/conversations/{preview.json()['id']}/shares", headers=h)
    assert (no.status_code, no.json()["error"]["code"]) == (400, "preview_not_shared")


async def test_a_share_opens_only_while_the_reader_may_use_the_assistant(
    client: AsyncClient, queued: list[uuid.UUID]
) -> None:
    """The assistant check alone: everything cited is readable, but the
    assistant is paused, so nobody but its editors may use it."""
    from app.models.assistant import Assistant, AssistantStatus

    org_id, people = await _org(client)
    h = _h(org_id, people["owner"])
    aid, _sid, hr, cid = await _shared_conversation(client, org_id, people)
    await _join(client, org_id, h, hr, people["sam"])
    pat = _h(org_id, people["pat"])
    made = await client.post(f"{API}/conversations/{cid}/shares", headers=pat)
    token = made.json()["url"].rsplit("/", 1)[-1]
    assert (await client.get(f"{API}/shared/{token}", headers=people["sam"].headers)).is_success
    async with get_sessionmaker()() as s:
        a = await s.get(Assistant, uuid.UUID(aid))
        assert a is not None
        a.status = AssistantStatus.archived
        await s.commit()
    r = await client.get(f"{API}/shared/{token}", headers=people["sam"].headers)
    assert (r.status_code, r.json()["error"]["code"]) == (403, "share_not_for_you")


async def test_a_share_citing_knowledge_outside_the_live_version_is_checked_too(
    client: AsyncClient, queued: list[uuid.UUID]
) -> None:
    """The citation check alone: the live version answers from an open
    source, so Sam may use it, but this conversation cites a restricted one
    the live version doesn't name."""
    org_id, people = await _org(client)
    h = _h(org_id, people["owner"])
    aid, _open = await _live_assistant(client, h)
    secret = await _source(client, h, aid, "Salaries.")
    hr = await _group(client, org_id, h, "HR")
    assert (await _restrict(client, h, aid, secret, [hr])).status_code == 200
    await go_live(client, aid, h)
    async with get_sessionmaker()() as s:
        conv = Conversation(
            assistant_id=uuid.UUID(aid),
            org_id=uuid.UUID(org_id),
            created_by=uuid.UUID(people["pat"].user_id),
            mode=ConversationMode.live,
        )
        s.add(conv)
        await s.flush()
        s.add(
            Message(
                conversation_id=conv.id,
                org_id=uuid.UUID(org_id),
                role=MessageRole.assistant,
                content="It's in the salaries file [1].",
                blocks=[{"type": "citation", "marker": 1, "data_source_id": secret}],
            )
        )
        await s.commit()
        cid = str(conv.id)
    sam = _h(org_id, people["sam"])
    assert "HR desk" in await _offered(client, sam), "Sam may use the assistant"
    made = await client.post(f"{API}/conversations/{cid}/shares", headers=_h(org_id, people["pat"]))
    token = made.json()["url"].rsplit("/", 1)[-1]
    r = await client.get(f"{API}/shared/{token}", headers=people["sam"].headers)
    assert (r.status_code, r.json()["error"]["code"]) == (403, "share_not_for_you")
