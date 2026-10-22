"""Requests that wait for an approver (Phase 9.1, plan D7 and D17).

In live Chat, a write the asker doesn't confirm themselves waits for one of
the assistant's approvers: admins by default, or the groups and people it
names, or whoever stands in for them. Never the asker. It waits as long as
the org says, the approvers are reminded once, and when someone decides,
the executor checks everything again, runs it once, and tells the asker.
"""

from __future__ import annotations

import asyncio
import base64
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from app.config import settings
from app.db.session import get_sessionmaker
from app.mail import backends
from app.models.approval import Approval, ApprovalDelegation, ApprovalStatus
from app.models.audit_log import AuditLog
from app.models.conversation import Conversation
from app.security.rotation import rotate_kek
from app.services import approvals as approvals_svc
from app.services import executor, retention
from httpx import AsyncClient
from sqlalchemy import select
from tests.pending import decide
from tests.test_chat_approvals import _assistant_with_db, _run_turn, fake_sql
from tests.versions import go_live

from test_orgs import Actor, _register  # type: ignore[import-not-found]

__all__ = ["fake_sql"]  # a fixture, imported for use by name

pytestmark = pytest.mark.anyio

API = "/api/v1"


@pytest.fixture(autouse=True)
def _empty_outbox() -> Any:
    backends.outbox.clear()
    yield
    backends.outbox.clear()


@dataclass
class Org:
    id: str
    people: dict[str, Actor]
    group_id: str

    def h(self, who: str) -> dict[str, str]:
        return {**self.people[who].headers, "X-Org-Id": self.id}


async def _org(client: AsyncClient) -> Org:
    """An owner, an admin, a builder, two employees, and a group "IT" with
    the second employee (Sam) in it."""
    owner = await _register(client, "owner@example.com")
    org = (await client.post(f"{API}/orgs", json={"name": "Acme"}, headers=owner.headers)).json()
    people = {"owner": owner}
    for name, role in (("admin", "admin"), ("builder", "member"), ("pat", None), ("sam", None)):
        actor = await _register(client, f"{name}@example.com")
        body = {"email": f"{name}@example.com", **({"role": role} if role else {})}
        inv = await client.post(f"{API}/orgs/{org['id']}/invites", json=body, headers=owner.headers)
        token = inv.json()["accept_url"].rsplit("/", 1)[-1]
        accepted = await client.post(f"{API}/invites/{token}/accept", headers=actor.headers)
        assert accepted.is_success, accepted.text
        people[name] = actor
    o = {**owner.headers, "X-Org-Id": org["id"]}
    group = (
        await client.post(f"{API}/orgs/{org['id']}/groups", json={"name": "IT"}, headers=o)
    ).json()
    added = await client.post(
        f"{API}/orgs/{org['id']}/groups/{group['id']}/members",
        json={"user_id": people["sam"].user_id},
        headers=o,
    )
    assert added.status_code == 201, added.text
    return Org(org["id"], people, group["id"])


@pytest.fixture
async def org(client: AsyncClient) -> Org:
    return await _org(client)


async def _live(client: AsyncClient, org: Org) -> str:
    """An approved assistant with a writable database, built by the builder."""
    aid = await _assistant_with_db(client, org.h("builder"))
    await go_live(client, aid, org.h("builder"), approver=org.h("owner"))
    return aid


async def _ask(client: AsyncClient, org: Org, aid: str, who: str = "pat") -> tuple[str, str]:
    """`who` asks for a write in live Chat. (conversation id, request id)."""
    r = await client.post(f"{API}/assistants/{aid}/conversations", json={}, headers=org.h(who))
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    events: list[Any] = []
    await _run_turn(cid, "sql: UPDATE laptops SET owner = 'pat' WHERE id = 7", events)
    (prompt,) = [e for e in events if e.type == "approval_required"]
    assert prompt.tier == "approver"
    return cid, prompt.approval_id


async def _row(approval_id: str) -> Approval:
    async with get_sessionmaker()() as s:
        row = await s.get(Approval, uuid.UUID(approval_id))
        assert row is not None
        return row


def _mail(kind: str) -> list[str]:
    return sorted(m.to for m in backends.outbox if m.kind == kind)


# ── who decides ──────────────────────────────────────────────


async def test_a_live_write_waits_for_an_approver_never_the_asker(
    client: AsyncClient, org: Org, fake_sql: dict[str, Any]
) -> None:
    aid = await _live(client, org)
    cid, rid = await _ask(client, org, aid)
    assert _mail("approval_waiting") == ["admin@example.com", "owner@example.com"]
    (told,) = [m for m in backends.outbox if m.kind == "approval_waiting"][:1]
    assert "UPDATE" not in told.text and "/approvals" in told.text

    own = await decide(client, org.h("pat"), rid, "approved")
    assert (own.status_code, own.json()["error"]["code"]) == (403, "cannot_decide_own")
    builder = await decide(client, org.h("builder"), rid, "approved")
    assert (builder.status_code, builder.json()["error"]["code"]) == (403, "not_an_approver")
    assert fake_sql["ran"] == []

    assert (await decide(client, org.h("admin"), rid, "approved")).status_code == 200
    await executor.drain()
    assert fake_sql["ran"] == ["UPDATE laptops SET owner = 'pat' WHERE id = 7"]
    assert (await _row(rid)).status is ApprovalStatus.succeeded
    assert _mail("approval_outcome") == ["pat@example.com"]
    # Pat reads how it ended, not what it did (no statement, no rows).
    said = [
        m["content"]
        for m in (
            await client.get(f"{API}/conversations/{cid}/messages", headers=org.h("pat"))
        ).json()["items"]
        if m["role"] == "system"
    ]
    ref = "R-" + uuid.UUID(rid).hex[:6].upper()
    assert said == [f"{ref} (A database change) was done."]
    builder_view = await client.get(
        f"{API}/conversations/{cid}/messages",
        headers={**org.h("owner"), "X-Read-Reason": "Checking a request"},
    )
    owner_said = [m["content"] for m in builder_view.json()["items"] if m["role"] == "system"]
    assert "1 row(s) affected" in owner_said[0], "an admin sees what it did"


async def test_an_employee_sees_where_it_stands_not_what_would_run(
    client: AsyncClient, org: Org, fake_sql: dict[str, Any]
) -> None:
    aid = await _live(client, org)
    cid, rid = await _ask(client, org, aid)
    (shown,) = (
        await client.get(f"{API}/conversations/{cid}/approvals", headers=org.h("pat"))
    ).json()["items"]
    assert (shown["id"], shown["status"], shown["tier"]) == (rid, "pending", "approver")
    assert (shown["input"], shown["rationale"]) == ({}, None)
    assert "UPDATE" not in str(shown)


async def test_an_assistants_named_approvers_decide_it(
    client: AsyncClient, org: Org, fake_sql: dict[str, Any]
) -> None:
    aid = await _live(client, org)
    url = f"{API}/assistants/{aid}/approvers"
    body = {"group_ids": [org.group_id], "user_ids": [org.people["builder"].user_id]}
    assert (await client.put(url, json=body, headers=org.h("builder"))).status_code == 403
    r = await client.put(url, json=body, headers=org.h("admin"))
    assert r.status_code == 200, r.text
    assert sorted(p["email"] for p in r.json()["people"]) == [
        "builder@example.com",
        "sam@example.com",
    ]
    assert r.json()["admins_decide"] is False
    seen = await client.get(url, headers=org.h("builder"))
    assert seen.json()["user_ids"] == [org.people["builder"].user_id], "its editors see who"

    _, rid = await _ask(client, org, aid)
    assert _mail("approval_waiting") == ["builder@example.com", "sam@example.com"]
    for who in ("sam", "builder", "owner"):
        queue = (await client.get(f"{API}/approvals/decidable", headers=org.h(who))).json()
        assert [q["id"] for q in queue] == [rid], who
    assert (await client.get(f"{API}/approvals/decidable", headers=org.h("pat"))).json() == []

    # Sam is an employee and an approver: deciding doesn't make him an admin.
    assert (await decide(client, org.h("sam"), rid, "approved")).status_code == 200
    audit = await _audit(rid, "approval.approved")
    assert audit["decider_role"] == "employee" and audit["self_approved"] is False


async def test_a_stand_in_decides_while_the_approver_is_away(
    client: AsyncClient, org: Org, fake_sql: dict[str, Any]
) -> None:
    aid = await _live(client, org)
    await client.put(
        f"{API}/assistants/{aid}/approvers",
        json={"group_ids": [], "user_ids": [org.people["builder"].user_id]},
        headers=org.h("admin"),
    )
    _, rid = await _ask(client, org, aid)
    away = f"{API}/approvals/delegations"
    tomorrow = (datetime.now(UTC) + timedelta(days=1)).isoformat()

    for body, code in (
        ({"email": "builder@example.com", "until": tomorrow}, "delegation_self"),
        ({"email": "nobody@example.com", "until": tomorrow}, "person_not_found"),
        (
            {
                "email": "pat@example.com",
                "until": (datetime.now(UTC) + timedelta(days=91)).isoformat(),
            },
            "delegation_too_long",
        ),
    ):
        r = await client.post(away, json=body, headers=org.h("builder"))
        assert r.json()["error"]["code"] == code

    r = await client.post(
        away, json={"email": "Pat@Example.com", "until": tomorrow}, headers=org.h("builder")
    )
    assert r.status_code == 201, r.text
    (handed,) = r.json()
    queue = (await client.get(f"{API}/approvals/decidable", headers=org.h("pat"))).json()
    assert queue == [], "Pat asked for it: still not theirs to decide"

    # Sam stands in instead; Pat's own request stays out of his own hands.
    await client.delete(f"{away}/{handed['id']}", headers=org.h("builder"))
    await client.post(
        away, json={"email": "sam@example.com", "until": tomorrow}, headers=org.h("builder")
    )
    queue = (await client.get(f"{API}/approvals/decidable", headers=org.h("sam"))).json()
    assert [q["id"] for q in queue] == [rid]

    # A hand-over past its date counts for nothing.
    async with get_sessionmaker()() as s:
        for d in (await s.scalars(select(ApprovalDelegation))).all():
            d.until = datetime.now(UTC) - timedelta(minutes=1)
        await s.commit()
    late = await decide(client, org.h("sam"), rid, "approved")
    assert (late.status_code, late.json()["error"]["code"]) == (403, "not_an_approver")


async def test_an_admins_own_powers_dont_pass_to_a_stand_in(
    client: AsyncClient, org: Org, fake_sql: dict[str, Any]
) -> None:
    aid = await _live(client, org)
    _, rid = await _ask(client, org, aid)
    tomorrow = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    r = await client.post(
        f"{API}/approvals/delegations",
        json={"email": "sam@example.com", "until": tomorrow},
        headers=org.h("admin"),
    )
    assert r.status_code == 201
    stand_in = await decide(client, org.h("sam"), rid, "approved")
    assert (stand_in.status_code, stand_in.json()["error"]["code"]) == (403, "not_an_approver")


async def test_declining_needs_a_note_and_tells_the_asker(
    client: AsyncClient, org: Org, fake_sql: dict[str, Any]
) -> None:
    aid = await _live(client, org)
    cid, rid = await _ask(client, org, aid)
    r = await client.post(
        f"{API}/approvals/{rid}:resolve",
        json={"decision": "denied", "input_sha256": (await _row(rid)).input_sha256},
        headers=org.h("admin"),
    )
    assert (r.status_code, r.json()["error"]["code"]) == (400, "note_required")
    r = await decide(client, org.h("admin"), rid, "denied", note="Laptops go through IT.")
    assert r.status_code == 200
    assert fake_sql["ran"] == []
    assert _mail("approval_outcome") == ["pat@example.com"]
    every = (await client.get(f"{API}/conversations/{cid}/messages", headers=org.h("pat"))).json()[
        "items"
    ]
    assert any("Laptops go through IT." in m["content"] for m in every if m["role"] == "system")


async def test_the_only_owner_may_decide_their_own_with_a_note(
    client: AsyncClient, org_headers: dict[str, str], fake_sql: dict[str, Any]
) -> None:
    """Nobody else can (7b.3's switch): allowed, with a reason, and recorded."""
    aid = await _assistant_with_db(client, org_headers)
    await go_live(client, aid, org_headers)
    cid = (
        await client.post(f"{API}/assistants/{aid}/conversations", json={}, headers=org_headers)
    ).json()["id"]
    events: list[Any] = []
    await _run_turn(cid, "sql: DELETE FROM sessions", events)
    (prompt,) = [e for e in events if e.type == "approval_required"]
    r = await client.post(
        f"{API}/approvals/{prompt.approval_id}:resolve",
        json={"decision": "approved", "input_sha256": prompt.input_sha256},
        headers=org_headers,
    )
    assert (r.status_code, r.json()["error"]["code"]) == (400, "note_required")
    r = await decide(client, org_headers, prompt.approval_id, "approved", note="Only me here.")
    assert r.status_code == 200
    assert (await _audit(prompt.approval_id, "approval.approved"))["self_approved"] is True


# ── running it ───────────────────────────────────────────────


@pytest.fixture
def held(monkeypatch: pytest.MonkeyPatch) -> list[uuid.UUID]:
    """Approvals are not handed to the executor: the test runs it."""
    handed: list[uuid.UUID] = []

    async def dispatch(approval_id: uuid.UUID) -> None:
        handed.append(approval_id)

    monkeypatch.setattr(executor, "dispatch", dispatch)
    return handed


async def test_everything_is_checked_again_before_it_runs(
    client: AsyncClient, org: Org, fake_sql: dict[str, Any], held: list[uuid.UUID]
) -> None:
    aid = await _live(client, org)
    _, paused = await _ask(client, org, aid)
    _, left = await _ask(client, org, aid, who="sam")
    for rid in (paused, left):
        assert (await decide(client, org.h("admin"), rid, "approved")).status_code == 200

    # Sam leaves; then the assistant is paused.
    r = await client.post(
        f"{API}/orgs/{org.id}/members/{org.people['sam'].user_id}:deactivate",
        json={},
        headers=org.h("owner"),
    )
    assert r.status_code == 200, r.text
    assert await executor.execute(uuid.UUID(left)) is ApprovalStatus.failed
    assert "no longer" in ((await _row(left)).result or "")

    r = await client.post(
        f"{API}/assistants/{aid}/archive", json={"reason": "Wrong data"}, headers=org.h("admin")
    )
    assert r.status_code == 200, r.text
    assert await executor.execute(uuid.UUID(paused)) is ApprovalStatus.failed
    assert "paused" in ((await _row(paused)).result or "")
    assert fake_sql["ran"] == [], "nothing ran that was no longer allowed"


async def test_it_runs_once_however_often_it_is_handed_over(
    client: AsyncClient, org: Org, fake_sql: dict[str, Any], held: list[uuid.UUID]
) -> None:
    aid = await _live(client, org)
    _, rid = await _ask(client, org, aid)
    await decide(client, org.h("admin"), rid, "approved")
    assert held == [uuid.UUID(rid)]
    results = await asyncio.gather(*(executor.execute(uuid.UUID(rid)) for _ in range(3)))
    assert sorted(str(r) for r in results) == ["None", "None", "succeeded"]
    assert len(fake_sql["ran"]) == 1
    actions = [a.action for a in await _audits(rid)]
    assert actions.count("approval.executed") == 1


async def test_the_approvers_are_reminded_once(
    client: AsyncClient, org: Org, fake_sql: dict[str, Any]
) -> None:
    aid = await _live(client, org)
    _, rid = await _ask(client, org, aid)
    backends.outbox.clear()
    await approvals_svc.sweep()
    assert _mail("approval_reminder") == [], "not yet"
    async with get_sessionmaker()() as s:
        row = await s.get(Approval, uuid.UUID(rid))
        assert row is not None
        row.created_at = datetime.now(UTC) - timedelta(hours=25)
        await s.commit()
    assert (await approvals_svc.sweep())["reminded"] == 1
    assert _mail("approval_reminder") == ["admin@example.com", "owner@example.com"]
    assert (await approvals_svc.sweep())["reminded"] == 0, "once"


async def test_a_key_rotation_moves_waiting_requests_too(
    client: AsyncClient, org: Org, fake_sql: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    aid = await _live(client, org)
    _, rid = await _ask(client, org, aid)
    before = approvals_svc.unseal(await _row(rid))
    old, new = settings.app_kek, base64.b64encode(os.urandom(32)).decode()
    async with get_sessionmaker()() as s:
        report = await rotate_kek(s, old_kek=old, new_kek=new)
    assert report.rotated >= 1
    monkeypatch.setattr(settings, "app_kek", new)
    assert approvals_svc.unseal(await _row(rid)) == before


async def test_retention_keeps_a_conversation_with_a_request_still_open(
    client: AsyncClient, org: Org, fake_sql: dict[str, Any], held: list[uuid.UUID]
) -> None:
    aid = await _live(client, org)
    cid, rid = await _ask(client, org, aid)
    async with get_sessionmaker()() as s:
        assert await retention.purge(s, uuid.UUID(org.id), [uuid.UUID(cid)]) == []
    await decide(client, org.h("admin"), rid, "denied")
    async with get_sessionmaker()() as s:
        assert await retention.purge(s, uuid.UUID(org.id), [uuid.UUID(cid)]) == [uuid.UUID(cid)]
        await s.commit()
        assert await s.get(Conversation, uuid.UUID(cid)) is None
    kept = await _row(rid)
    assert kept.conversation_id is None and kept.status is ApprovalStatus.denied, (
        "the record of a request outlives its conversation"
    )


async def _audits(approval_id: str) -> list[AuditLog]:
    async with get_sessionmaker()() as s:
        return list(
            (await s.scalars(select(AuditLog).where(AuditLog.target_id == approval_id))).all()
        )


async def _audit(approval_id: str, action: str) -> dict[str, Any]:
    (entry,) = [a for a in await _audits(approval_id) if a.action == action]
    meta = dict(entry.meta or {})
    assert meta["input_sha256"], "the hash of what was decided is kept"
    return meta
