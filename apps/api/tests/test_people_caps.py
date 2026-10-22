"""Per-person caps, the provider's spending limit, and alerts (Phase 8.9).

- **Whose spend**: the ledger records the person in whose conversation (or
  with whose AI helper) it happened; evals and indexing are the org's.
- **Per-person caps**: a default for each Employee, another for each
  builder, admin and owner, and overrides for one person. Reaching yours
  stops only you; Employees are told without money, at 80% and at 100%.
  Only admins see the caps and everyone's spend.
- **The provider's own limit** is its own failure, not "try again in a
  minute", and it reaches the admins.
- **Alerts**: what stops everyone is recorded, emailed to the admins at
  most once an hour, shown until an answer works or an admin marks it
  handled.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from app.agent import errors
from app.db.session import get_sessionmaker
from app.mail import backends
from app.models.alert import AdminAlert
from app.models.audit_log import AuditLog
from app.models.usage import UsageEvent, UsageKind
from app.services import alerts
from httpx import AsyncClient
from sqlalchemy import select, update
from tests.test_migrations import _alembic
from tests.test_version_review_migration import _insert
from tests.versions import go_live

from test_audiences import Org, _org  # type: ignore[import-not-found]

pytestmark = pytest.mark.anyio

API = "/api/v1"


@pytest.fixture
async def org(client: AsyncClient) -> Org:
    backends.outbox.clear()
    return await _org(client)


async def _live(client: AsyncClient, org: Org) -> str:
    h = org.h("builder")
    aid = (await client.post(f"{API}/assistants", json={"name": "Helpdesk"}, headers=h)).json()[
        "id"
    ]
    await go_live(client, aid, h, approver=org.h("owner"))
    return str(aid)


async def _spend(org: Org, who: str, usd: float) -> None:
    async with get_sessionmaker()() as s:
        s.add(
            UsageEvent(
                org_id=uuid.UUID(org.id),
                user_id=uuid.UUID(org.people[who].user_id),
                kind=UsageKind.llm,
                model="claude-sonnet-5-5",
                cost_usd=usd,
            )
        )
        await s.commit()


async def _ask(client: AsyncClient, org: Org, who: str, aid: str, **body: Any) -> list[dict]:
    conv = await client.post(f"{API}/assistants/{aid}/conversations", json=body, headers=org.h(who))
    assert conv.status_code == 201, conv.text
    r = await client.post(
        f"{API}/conversations/{conv.json()['id']}/messages",
        json={"text": "hello"},
        headers=org.h(who),
    )
    assert r.status_code == 200, r.text
    return [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]


async def _default(client: AsyncClient, org: Org, role: str, **limits: Any) -> Any:
    r = await client.put(
        f"{API}/orgs/{org.id}/budgets/people", json={"role": role, **limits}, headers=org.h("owner")
    )
    assert r.status_code == 200, r.text
    return r.json()


# ── whose spend ──────────────────────────────────────────────


async def test_a_turn_is_on_the_ledger_as_its_askers(client: AsyncClient, org: Org) -> None:
    aid = await _live(client, org)
    await _ask(client, org, "emp1", aid)
    async with get_sessionmaker()() as s:
        rows = list(await s.scalars(select(UsageEvent).where(UsageEvent.kind == UsageKind.llm)))
    assert rows and {r.user_id for r in rows} == {uuid.UUID(org.people["emp1"].user_id)}


def test_spend_already_there_is_given_its_asker(tmp_path: Path) -> None:
    db_path = tmp_path / "caps.db"
    assert (r := _alembic(db_path, "upgrade", "c8a7f2a3b4c5")).returncode == 0, r.stderr
    org, user, aid, conv, ev, evrun = (uuid.uuid4().hex for _ in range(6))
    db = sqlite3.connect(db_path)
    _insert(db, "organizations", id=org)
    _insert(db, "users", id=user, email="pat@example.com")
    _insert(db, "assistants", id=aid, org_id=org)
    _insert(db, "conversations", id=conv, org_id=org, assistant_id=aid, created_by=user)
    _insert(db, "conversations", id=ev, org_id=org, assistant_id=aid, created_by=user)
    db.execute("UPDATE conversations SET eval_run_id = ? WHERE id = ?", (evrun, ev))
    _insert(db, "usage_events", org_id=org, conversation_id=conv, kind="llm")
    _insert(db, "usage_events", org_id=org, conversation_id=ev, kind="llm")
    _insert(db, "usage_events", org_id=org, kind="embedding")
    db.commit()
    db.close()
    assert (r := _alembic(db_path, "upgrade", "c8a8a3b4c5d6")).returncode == 0, r.stderr
    db = sqlite3.connect(db_path)
    got = dict(db.execute("SELECT conversation_id, user_id FROM usage_events").fetchall())
    db.close()
    assert got[conv] == user and got[ev] is None and got[None] is None


# ── per-person caps ──────────────────────────────────────────


async def test_reaching_your_own_cap_stops_only_you(client: AsyncClient, org: Org) -> None:
    aid = await _live(client, org)
    await _default(client, org, "employee", daily_usd=1)
    await _spend(org, "emp1", 1.0)

    events = await _ask(client, org, "emp1", aid)
    assert [e["type"] for e in events] == ["error"]
    assert events[0]["code"] == "person_limit" and events[0]["resets_at"]
    # In an Employee's words: no money.
    assert events[0]["message"] == "You've reached your limit for now."
    # Everyone else carries on, and nothing is raised for the admins.
    assert any(e["type"] == "done" for e in await _ask(client, org, "emp2", aid))
    async with get_sessionmaker()() as s:
        assert list(await s.scalars(select(AdminAlert))) == []


async def test_an_employee_is_told_at_80_percent_without_money(
    client: AsyncClient, org: Org
) -> None:
    aid = await _live(client, org)
    await _default(client, org, "employee", daily_usd=1)
    await _spend(org, "emp1", 0.85)
    events = await _ask(client, org, "emp1", aid)
    (note,) = [e for e in events if e["type"] == "budget"]
    assert note["scope"] == "person"
    assert note["message"] == "You've used most of your allowance for today."
    assert any(e["type"] == "done" for e in events)


async def test_an_override_replaces_the_default_for_one_person(
    client: AsyncClient, org: Org
) -> None:
    aid = await _live(client, org)
    await _default(client, org, "employee", daily_usd=1)
    await _spend(org, "emp1", 2.0)
    url = f"{API}/orgs/{org.id}/members/{org.people['emp1'].user_id}/budget"
    raised = await client.put(url, json={"daily_usd": 5}, headers=org.h("owner"))
    assert raised.status_code == 200, raised.text
    assert any(e["type"] == "done" for e in await _ask(client, org, "emp1", aid))
    back = await client.put(url, json={"daily_usd": None}, headers=org.h("owner"))
    assert back.status_code == 200
    assert (await _ask(client, org, "emp1", aid))[0]["code"] == "person_limit"
    async with get_sessionmaker()() as s:
        scopes = [
            (a.meta or {}).get("scope")
            for a in await s.scalars(select(AuditLog).where(AuditLog.action == "budget.updated"))
        ]
    assert (
        "role:employee" in scopes
        and scopes.count(f"person:{uuid.UUID(org.people['emp1'].user_id).hex}") == 2
    )


async def test_a_builders_previews_count_and_builders_have_their_own_default(
    client: AsyncClient, org: Org
) -> None:
    aid = await _live(client, org)
    await _default(client, org, "employee", daily_usd=100)
    await _default(client, org, "builder", daily_usd=1)
    await _spend(org, "builder", 1.0)
    events = await _ask(client, org, "builder", aid, mode="preview")
    assert events[0]["code"] == "person_limit"
    # A builder reads it with the figure.
    assert "$1.00" in events[0]["message"] and events[0]["message"].startswith("Your daily limit")
    await _spend(org, "emp1", 1.0)
    assert any(e["type"] == "done" for e in await _ask(client, org, "emp1", aid))


async def test_only_admins_see_the_caps_and_everyones_spend(client: AsyncClient, org: Org) -> None:
    await _default(client, org, "employee", daily_usd=2, monthly_usd=20)
    await _spend(org, "emp1", 0.5)
    got = (await client.get(f"{API}/orgs/{org.id}/budgets/people", headers=org.h("owner"))).json()
    assert {d["role"]: d["daily_usd"] for d in got["defaults"]} == {
        "employee": 2.0,
        "builder": None,
    }
    emp1 = next(p for p in got["people"] if p["email"] == "emp1@example.com")
    assert (emp1["spent_today_usd"], emp1["daily_usd"], emp1["monthly_usd"]) == (0.5, 2.0, 20.0)
    assert emp1["own_daily"] is False
    builder = next(p for p in got["people"] if p["email"] == "builder@example.com")
    assert builder["daily_usd"] is None
    for who in ("builder", "emp1"):
        r = await client.get(f"{API}/orgs/{org.id}/budgets/people", headers=org.h(who))
        assert r.status_code == 403, who
    # The budgets everyone may see don't include the per-person ones.
    shared = (await client.get(f"{API}/orgs/{org.id}/budgets", headers=org.h("builder"))).json()
    assert shared["budgets"] == []
    stranger = await client.put(
        f"{API}/orgs/{org.id}/members/{uuid.uuid4()}/budget",
        json={"daily_usd": 1},
        headers=org.h("owner"),
    )
    assert stranger.status_code == 404


async def test_employees_get_a_smaller_message_rate(
    client: AsyncClient, org: Org, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []

    async def enforce(bucket: str, _subject: str, _spec: str, **_kw: Any) -> None:
        seen.append(bucket)

    monkeypatch.setattr("app.api.ratelimit.enforce", enforce)
    aid = await _live(client, org)
    await _ask(client, org, "emp1", aid)
    await _ask(client, org, "builder", aid)
    assert "chat_employee" in seen and "chat_user" in seen
    assert seen.index("chat_employee") < seen.index("chat_user")


# ── the provider's own limit ─────────────────────────────────


@pytest.mark.parametrize(
    ("status", "text"),
    [
        (429, '{"type":"rate_limit_error","code":"enforced_spend_limit_reached"}'),
        (400, "You have reached your specified API usage limits."),
    ],
)
def test_the_providers_spending_limit_is_its_own_failure(status: int, text: str) -> None:
    code = errors._api_failure(status, text)
    assert code == "provider_spend_cap"
    assert errors.failure(code).retryable is False


def test_an_ordinary_rate_limit_is_still_worth_retrying() -> None:
    code = errors._api_failure(429, "rate limit exceeded")
    assert code == "rate_limited" and errors.failure(code).retryable is True


# ── alerts ───────────────────────────────────────────────────


async def test_an_alert_emails_the_admins_once_an_hour_and_an_answer_clears_it(
    client: AsyncClient, org: Org
) -> None:
    oid = uuid.UUID(org.id)
    await alerts.raise_alert(oid, "provider_spend_cap", detail="enforced_spend_limit_reached")
    await alerts.raise_alert(oid, "provider_spend_cap")
    async with get_sessionmaker()() as s:
        (row,) = list(await s.scalars(select(AdminAlert)))
    assert (row.kind, row.count) == ("provider_spend_cap", 2)
    sent = [m for m in backends.outbox if m.kind == "admin_alert"]
    # The owner only: no other admin in this org. Once, not twice.
    assert [m.to for m in sent] == ["owner@example.com"]
    assert "spending limit" in sent[0].subject

    listed = await client.get(f"{API}/orgs/{org.id}/alerts?open=true", headers=org.h("owner"))
    assert [a["kind"] for a in listed.json()] == ["provider_spend_cap"]
    assert (
        await client.get(f"{API}/orgs/{org.id}/alerts", headers=org.h("emp1"))
    ).status_code == 403

    await alerts.resolve_after_answer(oid, None)
    still = await client.get(f"{API}/orgs/{org.id}/alerts?open=true", headers=org.h("owner"))
    assert still.json() == []


async def test_busy_is_raised_only_after_several_in_a_few_minutes(org: Org) -> None:
    oid = uuid.UUID(org.id)
    for _ in range(alerts.BUSY_ALERT_AT - 1):
        await alerts.raise_alert(oid, "busy")
    assert not [m for m in backends.outbox if m.kind == "admin_alert"]
    await alerts.raise_alert(oid, "busy")
    assert len([m for m in backends.outbox if m.kind == "admin_alert"]) == 1
    # Quiet for the window: the next one starts a new count.
    async with get_sessionmaker()() as s:
        await s.execute(
            update(AdminAlert).values(last_at=datetime.now(UTC) - timedelta(minutes=10))
        )
        await s.commit()
    await alerts.raise_alert(oid, "busy")
    async with get_sessionmaker()() as s:
        (row,) = list(await s.scalars(select(AdminAlert)))
    assert row.count == 1


async def test_the_org_budget_used_up_raises_an_alert_an_admin_can_clear(
    client: AsyncClient, org: Org
) -> None:
    aid = await _live(client, org)
    r = await client.put(
        f"{API}/orgs/{org.id}/budgets", json={"daily_usd": 1}, headers=org.h("owner")
    )
    assert r.status_code == 200
    await _spend(org, "emp2", 1.0)
    events = await _ask(client, org, "emp1", aid)
    assert events[0]["code"] == "budget_exceeded"
    listed = (
        await client.get(f"{API}/orgs/{org.id}/alerts?open=true", headers=org.h("owner"))
    ).json()
    assert [a["kind"] for a in listed] == ["budget_org"]

    cleared = await client.post(
        f"{API}/orgs/{org.id}/alerts:resolve", json={"kind": "budget_org"}, headers=org.h("owner")
    )
    assert cleared.json() == {"resolved": 1}
    async with get_sessionmaker()() as s:
        audited = await s.scalar(select(AuditLog).where(AuditLog.action == "alert.resolve"))
    assert audited is not None and audited.meta["kind"] == "budget_org"
    assert (
        await client.get(f"{API}/orgs/{org.id}/alerts?open=true", headers=org.h("owner"))
    ).json() == []


async def test_a_failed_turn_raises_its_alert(
    client: AsyncClient, org: Org, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.agent import runtime as runtime_mod
    from app.agent.events import ErrorEvent

    class Capped:
        name = "capped"

        async def stream(self, **_kwargs: Any) -> Any:
            yield ErrorEvent(code="provider_spend_cap", message="limit reached", retryable=False)

    aid = await _live(client, org)
    monkeypatch.setattr(runtime_mod, "get_driver", Capped)
    events = await _ask(client, org, "emp1", aid)
    assert any(e.get("code") == "provider_spend_cap" for e in events)
    async with get_sessionmaker()() as s:
        rows = list(await s.scalars(select(AdminAlert)))
    # Still open: the failed turn's own `done` doesn't clear it.
    assert [(a.kind, a.resolved_at) for a in rows] == [("provider_spend_cap", None)]
