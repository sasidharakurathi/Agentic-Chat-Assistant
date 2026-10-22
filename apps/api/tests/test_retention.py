"""One purge path, retention and legal holds (Phase 8.10).

Erasing is `services.retention.purge`, used by the nightly retention rule
and by the owner erasing one person. It reaches every place a
conversation's content lives (the rows, its memory notes, the CLI's
transcript, its session key) and skips what is held or answering. Each
purge is recorded, ids only, to re-apply after a restore. Tracing carries
metadata only in production.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from app.agent import cli_files
from app.config import settings
from app.db.session import get_sessionmaker
from app.models.audit_log import AuditLog
from app.models.conversation import Conversation, Message, MessageRole
from app.models.feedback import ConversationOutcome, MessageFeedback
from app.models.memory import MemoryFile
from app.models.retention import Erasure
from app.models.usage import UsageEvent
from app.observability import turn_trace
from app.preflight import Report, review_tracing
from app.services import retention, turns
from httpx import AsyncClient
from sqlalchemy import func, select, update
from tests.versions import go_live

from test_audiences import Org, _org  # type: ignore[import-not-found]

pytestmark = pytest.mark.anyio

API = "/api/v1"


@pytest.fixture
async def org(client: AsyncClient) -> Org:
    return await _org(client)


async def _live(client: AsyncClient, org: Org) -> str:
    h = org.h("builder")
    aid = (await client.post(f"{API}/assistants", json={"name": "Helpdesk"}, headers=h)).json()[
        "id"
    ]
    await go_live(client, aid, h, approver=org.h("owner"))
    return str(aid)


async def _chat(client: AsyncClient, org: Org, who: str, aid: str) -> uuid.UUID:
    r = await client.post(f"{API}/assistants/{aid}/conversations", json={}, headers=org.h(who))
    cid = r.json()["id"]
    said = await client.post(
        f"{API}/conversations/{cid}/messages", json={"text": "hello"}, headers=org.h(who)
    )
    assert said.status_code == 200, said.text
    return uuid.UUID(cid)


async def _age(cid: uuid.UUID, days: int) -> None:
    then = datetime.now(UTC) - timedelta(days=days)
    async with get_sessionmaker()() as s:
        await s.execute(
            update(Conversation)
            .where(Conversation.id == cid)
            .values(last_message_at=then, created_at=then)
        )
        await s.commit()


async def _exists(cid: uuid.UUID) -> bool:
    async with get_sessionmaker()() as s:
        return (await s.get(Conversation, cid)) is not None


async def _with_leftovers(org: Org, aid: str, cid: uuid.UUID, who: str) -> None:
    """Memory notes, feedback, an outcome and a transcript, like a real one."""
    user = uuid.UUID(org.people[who].user_id)
    async with get_sessionmaker()() as s:
        answer = await s.scalar(
            select(Message).where(
                Message.conversation_id == cid, Message.role == MessageRole.assistant
            )
        )
        assert answer is not None
        for key in (f"conv:{cid}", f"user:{user}"):
            s.add(
                MemoryFile(
                    org_id=uuid.UUID(org.id),
                    assistant_id=uuid.UUID(aid),
                    owner_key=key,
                    path="/memories/notes.md",
                    content="on leave in May",
                )
            )
        s.add(
            MessageFeedback(
                org_id=uuid.UUID(org.id),
                assistant_id=uuid.UUID(aid),
                conversation_id=cid,
                message_id=answer.id,
                user_id=user,
                rating="down",
                comment="wrong",
            )
        )
        s.add(
            ConversationOutcome(
                org_id=uuid.UUID(org.id),
                assistant_id=uuid.UUID(aid),
                conversation_id=cid,
                solved=False,
            )
        )
        await s.commit()
    transcript = cli_files.config_dir() / "projects" / f"-scratch-{cid}"
    transcript.mkdir(parents=True, exist_ok=True)
    (transcript / "abc.jsonl").write_text("{}", encoding="utf-8")


# ── the one purge ────────────────────────────────────────────


async def test_a_purge_reaches_everywhere_and_keeps_the_totals(
    client: AsyncClient, org: Org
) -> None:
    aid = await _live(client, org)
    cid = await _chat(client, org, "emp1", aid)
    await _with_leftovers(org, aid, cid, "emp1")
    async with get_sessionmaker()() as s:
        spent_before = await s.scalar(select(func.sum(UsageEvent.cost_usd)))
        gone = await retention.purge(s, uuid.UUID(org.id), [cid])
    assert gone == [cid]
    async with get_sessionmaker()() as s:
        assert await s.get(Conversation, cid) is None
        assert (
            await s.scalar(
                select(func.count()).select_from(Message).where(Message.conversation_id == cid)
            )
            == 0
        )
        assert await s.scalar(select(func.count()).select_from(MessageFeedback)) == 0
        keys = list(await s.scalars(select(MemoryFile.owner_key)))
        # The conversation's notes go; the person's own stay (theirs to erase).
        assert keys == [f"user:{org.people['emp1'].user_id}"]
        (outcome,) = list(await s.scalars(select(ConversationOutcome)))
        assert outcome.conversation_id is None
        assert await s.scalar(select(func.sum(UsageEvent.cost_usd))) == spent_before
    assert not list((cli_files.config_dir() / "projects").glob(f"*{cid}"))


async def test_a_purge_skips_held_and_answering_conversations(
    client: AsyncClient, org: Org, monkeypatch: pytest.MonkeyPatch
) -> None:
    aid = await _live(client, org)
    held = await _chat(client, org, "emp1", aid)
    busy = await _chat(client, org, "emp2", aid)
    free = await _chat(client, org, "emp2", aid)
    r = await client.post(
        f"{API}/orgs/{org.id}/holds",
        json={"conversation_id": str(held), "reason": "Complaint 12"},
        headers=org.h("owner"),
    )
    assert r.status_code == 201, r.text

    async def running(cid: Any) -> str | None:
        return "t" if uuid.UUID(str(cid)) == busy else None

    monkeypatch.setattr(turns, "running_turn", running)
    async with get_sessionmaker()() as s:
        gone = await retention.purge(s, uuid.UUID(org.id), [held, busy, free])
    assert gone == [free]
    assert await _exists(held) and await _exists(busy)


# ── the retention rule ───────────────────────────────────────


async def test_retention_erases_what_is_older_than_the_setting(
    client: AsyncClient, org: Org
) -> None:
    aid = await _live(client, org)
    old, recent, theirs = [await _chat(client, org, w, aid) for w in ("emp1", "emp1", "emp2")]
    await _age(old, 400)
    await _age(theirs, 400)
    hold = await client.post(
        f"{API}/orgs/{org.id}/holds",
        json={"user_id": org.people["emp2"].user_id, "reason": "Investigation"},
        headers=org.h("owner"),
    )
    assert hold.status_code == 201
    async with get_sessionmaker()() as s:
        assert await retention.apply_retention(s, uuid.UUID(org.id)) == 1
    assert not await _exists(old)
    assert await _exists(recent) and await _exists(theirs)
    async with get_sessionmaker()() as s:
        (e,) = list(await s.scalars(select(Erasure)))
    assert e.kind == "retention" and e.conversation_ids == [str(old)] and e.cutoff is not None

    # Lifted, the held one goes the next night.
    await client.post(
        f"{API}/orgs/{org.id}/holds:release",
        json={"hold_id": hold.json()["id"]},
        headers=org.h("owner"),
    )
    assert await retention.retention_sweep() == 1
    assert not await _exists(theirs)


async def test_admins_set_the_retention_and_a_short_one_is_flagged(
    client: AsyncClient, org: Org
) -> None:
    url = f"{API}/orgs/{org.id}/retention"
    r = await client.put(url, json={"days": 90}, headers=org.h("owner"))
    assert r.json() == {"days": 90, "below_floor": True}
    assert (await client.put(url, json={"days": 29}, headers=org.h("owner"))).status_code == 422
    assert (await client.put(url, json={"days": 400}, headers=org.h("builder"))).status_code == 403
    settings_out = (
        await client.get(f"{API}/orgs/{org.id}/settings", headers=org.h("owner"))
    ).json()
    assert settings_out["retention_days"] == 90
    async with get_sessionmaker()() as s:
        row = await s.scalar(select(AuditLog).where(AuditLog.action == "org.retention.update"))
    assert row is not None and row.meta == {"from": 365, "to": 90}

    aid = await _live(client, org)
    cid = await _chat(client, org, "emp1", aid)
    await _age(cid, 100)
    async with get_sessionmaker()() as s:
        assert await retention.apply_retention(s, uuid.UUID(org.id)) == 1


# ── erasing one person ───────────────────────────────────────


async def test_the_owner_erases_one_persons_data(client: AsyncClient, org: Org) -> None:
    aid = await _live(client, org)
    cid = await _chat(client, org, "emp1", aid)
    other = await _chat(client, org, "emp2", aid)
    await _with_leftovers(org, aid, cid, "emp1")
    user = org.people["emp1"].user_id
    url = f"{API}/orgs/{org.id}/members/{user}:erase"

    wrong = await client.post(
        url, json={"confirm_email": "emp2@example.com"}, headers=org.h("owner")
    )
    assert (wrong.status_code, wrong.json()["error"]["code"]) == (400, "confirm_email_mismatch")
    assert (
        await client.post(url, json={"confirm_email": "emp1@example.com"}, headers=org.h("builder"))
    ).status_code == 403

    r = await client.post(url, json={"confirm_email": "EMP1@example.com"}, headers=org.h("owner"))
    assert r.status_code == 200, r.text
    assert r.json() == {"conversations": 1, "kept": 0, "memories": 1, "comments": 0}
    assert not await _exists(cid) and await _exists(other)
    async with get_sessionmaker()() as s:
        assert list(await s.scalars(select(MemoryFile))) == []
        named = await s.scalar(
            select(func.count())
            .select_from(UsageEvent)
            .where(UsageEvent.user_id == uuid.UUID(user))
        )
        assert named == 0
        audit = await s.scalar(select(AuditLog).where(AuditLog.action == "person.erase"))
        erasure = await s.scalar(select(Erasure).where(Erasure.kind == "person"))
    assert audit is not None and audit.meta["conversations"] == 1
    assert "hello" not in str(audit.meta)
    assert erasure is not None and erasure.user_id == uuid.UUID(user)
    # They are still a member; their account stays.
    members = (await client.get(f"{API}/orgs/{org.id}/members", headers=org.h("owner"))).json()
    assert any(m["user_id"] == user for m in members["items"])


async def test_a_held_person_cant_be_erased(client: AsyncClient, org: Org) -> None:
    user = org.people["emp1"].user_id
    await client.post(
        f"{API}/orgs/{org.id}/holds",
        json={"user_id": user, "reason": "POSH inquiry"},
        headers=org.h("owner"),
    )
    r = await client.post(
        f"{API}/orgs/{org.id}/members/{user}:erase",
        json={"confirm_email": "emp1@example.com"},
        headers=org.h("owner"),
    )
    assert (r.status_code, r.json()["error"]["code"]) == (409, "on_legal_hold")


# ── holds ────────────────────────────────────────────────────


async def test_holds_are_the_owners_and_admins_can_see_them(client: AsyncClient, org: Org) -> None:
    url = f"{API}/orgs/{org.id}/holds"
    both = await client.post(
        url,
        json={
            "user_id": org.people["emp1"].user_id,
            "conversation_id": str(uuid.uuid4()),
            "reason": "x" * 5,
        },
        headers=org.h("owner"),
    )
    assert (both.status_code, both.json()["error"]["code"]) == (400, "hold_target")
    made = await client.post(
        url,
        json={"user_id": org.people["emp1"].user_id, "reason": "Dispute"},
        headers=org.h("owner"),
    )
    assert made.status_code == 201 and made.json()["user_email"] == "emp1@example.com"
    assert (
        await client.post(
            url,
            json={"user_id": org.people["emp2"].user_id, "reason": "No"},
            headers=org.h("builder"),
        )
    ).status_code == 403
    listed = (await client.get(url, headers=org.h("owner"))).json()
    assert [h["reason"] for h in listed] == ["Dispute"]
    released = await client.post(
        f"{url}:release", json={"hold_id": made.json()["id"]}, headers=org.h("owner")
    )
    assert released.json()["released_at"]
    async with get_sessionmaker()() as s:
        actions = [
            a.action for a in await s.scalars(select(AuditLog).order_by(AuditLog.created_at))
        ]
    assert "legal_hold.create" in actions and "legal_hold.release" in actions


# ── after a restore ──────────────────────────────────────────


async def test_erasures_are_applied_again_after_a_restore(client: AsyncClient, org: Org) -> None:
    aid = await _live(client, org)
    cid = await _chat(client, org, "emp1", aid)
    await _age(cid, 400)
    async with get_sessionmaker()() as s:
        await retention.apply_retention(s, uuid.UUID(org.id))
        # A restore brings it back (the row, as the backup had it).
        s.add(
            Conversation(
                id=cid,
                org_id=uuid.UUID(org.id),
                assistant_id=uuid.UUID(aid),
                created_by=uuid.UUID(org.people["emp1"].user_id),
                title="Back from a backup",
            )
        )
        await s.commit()
    assert await _exists(cid)
    async with get_sessionmaker()() as s:
        assert await retention.reapply(s) == 1
    assert not await _exists(cid)


async def test_a_saved_record_puts_back_what_the_restore_rolled_back(
    client: AsyncClient, org: Org
) -> None:
    """The restore rolls back `erasures` too: restore.sh saves it first."""
    aid = await _live(client, org)
    cid = await _chat(client, org, "emp1", aid)
    await _age(cid, 400)
    async with get_sessionmaker()() as s:
        await retention.apply_retention(s, uuid.UUID(org.id))
        (e,) = list(await s.scalars(select(Erasure)))
        # As `row_to_json` writes it.
        saved = {
            "id": str(e.id),
            "org_id": str(e.org_id),
            "kind": e.kind,
            "user_id": None,
            "cutoff": e.cutoff.isoformat() if e.cutoff else None,
            "conversation_ids": e.conversation_ids,
            "memories": 0,
            "comments": 0,
            "created_by": None,
            "created_at": e.created_at.isoformat(),
        }
        # The backup had neither the erasure nor the absence of the row.
        await s.delete(e)
        s.add(
            Conversation(
                id=cid,
                org_id=uuid.UUID(org.id),
                assistant_id=uuid.UUID(aid),
                created_by=uuid.UUID(org.people["emp1"].user_id),
                title="Back from a backup",
            )
        )
        await s.commit()
    async with get_sessionmaker()() as s:
        stranger = {**saved, "id": str(uuid.uuid4()), "org_id": str(uuid.uuid4())}
        assert await retention.load_erasures(s, [saved, saved, stranger]) == 1
        assert await retention.reapply(s) == 1
    assert not await _exists(cid)


# ── tracing ──────────────────────────────────────────────────


def test_production_traces_carry_metadata_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "trace_content", "")
    assert settings.trace_full is False
    assert turn_trace._content("the VPN keeps dropping") == turn_trace.WITHHELD
    monkeypatch.setattr(settings, "trace_content", "full")
    assert settings.trace_full is True
    assert "VPN keeps dropping" in turn_trace._content("the VPN keeps dropping")
    monkeypatch.setattr(settings, "app_env", "dev")
    monkeypatch.setattr(settings, "trace_content", "")
    assert settings.trace_full is True


def test_preflight_warns_about_full_traces_in_production() -> None:
    report = Report()
    review_tracing(
        SimpleNamespace(
            is_production=True,
            langfuse_host="https://lf",
            otel_exporter_otlp_endpoint="",
            trace_full=True,
        ),
        report,
    )
    assert any(f.name == "TRACE_CONTENT" for f in report.findings)
    quiet = Report()
    review_tracing(
        SimpleNamespace(
            is_production=True,
            langfuse_host="https://lf",
            otel_exporter_otlp_endpoint="",
            trace_full=False,
        ),
        quiet,
    )
    assert not quiet.findings
