"""Org and assistant budgets (task 5.7): what's spent, 80% and 100%.

Spend is whatever is on the `usage_events` ledger; these tests put rows
there directly, dated where they need to be.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from app.agent import claude_api
from app.db.session import get_sessionmaker
from app.models.assistant import Assistant
from app.models.audit_log import AuditLog
from app.models.budget import BudgetPeriod
from app.models.conversation import Message
from app.models.usage import UsageEvent, UsageKind
from app.rag import ingest
from app.services import budgets
from app.services.budgets import BudgetStatus, Gate, narrower, period_bounds
from httpx import AsyncClient
from sqlalchemy import select
from tests import claude_stub

from test_orgs import _register  # type: ignore[import-not-found]

# ── periods and states ───────────────────────────────────────


def test_periods_are_the_utc_day_and_month() -> None:
    now = datetime(2026, 12, 31, 22, 15, tzinfo=UTC)
    assert period_bounds(BudgetPeriod.day, now) == (
        datetime(2026, 12, 31, tzinfo=UTC),
        datetime(2027, 1, 1, tzinfo=UTC),
    )
    assert period_bounds(BudgetPeriod.month, now) == (
        datetime(2026, 12, 1, tzinfo=UTC),
        datetime(2027, 1, 1, tzinfo=UTC),
    )
    feb = datetime(2027, 2, 28, 23, 59, tzinfo=UTC)
    assert period_bounds(BudgetPeriod.month, feb)[1] == datetime(2027, 3, 1, tzinfo=UTC)
    # Another zone's clock is converted, not read as UTC: 03:00 on 1 October
    # in India is still 30 September in UTC.
    ist = datetime(2026, 10, 1, 3, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    assert period_bounds(BudgetPeriod.day, ist)[0] == datetime(2026, 9, 30, tzinfo=UTC)


def _status(spent: float, *, scope: Any = "org", period: Any = BudgetPeriod.day) -> BudgetStatus:
    return BudgetStatus(
        scope=scope,
        assistant_id=None if scope == "org" else uuid.uuid4(),
        period=period,
        limit_usd=10.0,
        spent_usd=spent,
        resets_at=datetime(2026, 11, 1, tzinfo=UTC),
    )


def test_warning_from_80_percent_and_a_stop_at_100() -> None:
    assert _status(7.99).state == "ok"
    assert _status(8.0).state == "warning"
    assert _status(9.99).state == "warning"
    assert _status(10.0).state == "exceeded"
    assert _status(8.5).warning_message() == (
        "This organisation's daily budget is 85% used ($8.50 of $10.00)."
    )
    assert _status(10.0).exceeded_message() == (
        "This organisation's daily budget of $10.00 is used up. It resets at midnight UTC."
    )
    monthly = _status(12.0, scope="assistant", period=BudgetPeriod.month)
    assert monthly.exceeded_message() == (
        "This assistant's monthly budget of $10.00 is used up. It resets on 1 November (UTC)."
    )
    assert monthly.remaining_usd == 0.0


def test_money_reads_as_people_read_it() -> None:
    """Rounded to cents, $0.0367 of $0.045 read "$0.04 of $0.04"."""
    assert [budgets.usd(x) for x in (0.0367, 0.045, 0.5, 0.01, 12.345, 0.00004)] == [
        "$0.0367",
        "$0.045",
        "$0.50",
        "$0.01",
        "$12.35",
        "$0.00",
    ]
    status = _status(0.0367)
    status.limit_usd = 0.045
    assert status.warning_message() == (
        "This organisation's daily budget is 81% used ($0.0367 of $0.045)."
    )


def test_a_turn_gets_the_tighter_of_its_own_cap_and_the_budgets() -> None:
    gate = Gate([_status(9.0), _status(2.0, scope="assistant")])
    assert gate.tightest is gate.statuses[0]
    assert narrower(0.5, gate) == (0.5, None), "the conversation's cap is tighter"
    remaining, message = narrower(None, gate)
    assert remaining == pytest.approx(1.0)
    assert message == gate.statuses[0].exceeded_message()
    assert narrower(3.0, Gate()) == (3.0, None)


# ── spend from the ledger ────────────────────────────────────


async def _assistant(client: AsyncClient, headers: dict[str, str], name: str = "Budgeted") -> str:
    r = await client.post("/api/v1/assistants", json={"name": name}, headers=headers)
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def _spend(
    org_id: str, usd: float, *, assistant_id: str | None = None, at: datetime | None = None
) -> None:
    async with get_sessionmaker()() as s:
        s.add(
            UsageEvent(
                org_id=uuid.UUID(org_id),
                assistant_id=uuid.UUID(assistant_id) if assistant_id else None,
                kind=UsageKind.llm,
                model="claude-sonnet-5",
                cost_usd=usd,
                created_at=at or datetime.now(UTC),
            )
        )
        await s.commit()


async def _put(client: AsyncClient, headers: dict[str, str], url: str, **limits: Any) -> Any:
    r = await client.put(url, json=limits, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def test_spend_counts_only_this_period_and_this_scope(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    org = org_headers["X-Org-Id"]
    a1 = await _assistant(client, org_headers, "One")
    a2 = await _assistant(client, org_headers, "Two")
    now = datetime.now(UTC)
    day_start, _ = period_bounds(BudgetPeriod.day, now)
    month_start, _ = period_bounds(BudgetPeriod.month, now)
    await _spend(org, 1.0, assistant_id=a1)
    await _spend(org, 2.0, assistant_id=a2)
    await _spend(org, 4.0, assistant_id=a1, at=day_start - timedelta(seconds=1))
    await _spend(org, 8.0, at=month_start - timedelta(seconds=1))

    await _put(client, org_headers, f"/api/v1/orgs/{org}/budgets", daily_usd=10, monthly_usd=100)
    body = await _put(client, org_headers, f"/api/v1/assistants/{a1}/budget", daily_usd=5)
    spent = {(b["scope"], b["period"]): b["spent_usd"] for b in body["budgets"]}
    same_month = day_start - timedelta(seconds=1) >= month_start
    assert spent == {
        ("org", "day"): pytest.approx(3.0),
        ("org", "month"): pytest.approx(7.0 if same_month else 3.0),
        ("assistant", "day"): pytest.approx(1.0),
    }
    listing = (await client.get(f"/api/v1/orgs/{org}/budgets", headers=org_headers)).json()
    assert [(b["scope"], b["period"], b["assistant_name"]) for b in listing["budgets"]] == [
        ("org", "day", None),
        ("org", "month", None),
        ("assistant", "day", "One"),
    ]
    assert listing["can_edit"] is True


# ── changing them ────────────────────────────────────────────


async def test_only_admins_change_budgets_and_every_change_is_logged(
    client: AsyncClient,
) -> None:
    owner = await _register(client, "budget-owner@example.com")
    member = await _register(client, "budget-member@example.com")
    org = (
        await client.post("/api/v1/orgs", json={"name": "Budget Team"}, headers=owner.headers)
    ).json()
    inv = await client.post(
        f"/api/v1/orgs/{org['id']}/invites",
        json={"email": "budget-member@example.com", "role": "member"},
        headers=owner.headers,
    )
    token = inv.json()["accept_url"].rsplit("/", 1)[-1]
    await client.post(f"/api/v1/invites/{token}/accept", headers=member.headers)
    o = {**owner.headers, "X-Org-Id": org["id"]}
    m = {**member.headers, "X-Org-Id": org["id"]}
    aid = await _assistant(client, m, "Member's")
    base = f"/api/v1/orgs/{org['id']}/budgets"

    assert (await client.put(base, json={"daily_usd": 5}, headers=m)).status_code == 403
    mine = f"/api/v1/assistants/{aid}/budget"
    denied = await client.put(mine, json={"daily_usd": 5}, headers=m)
    assert denied.status_code == 403, "even on an assistant they created"
    seen = (await client.get(base, headers=m)).json()
    assert seen == {"budgets": [], "can_edit": False}, "members can see, not change"

    await _put(client, o, base, daily_usd=5, monthly_usd=50)
    await _put(client, o, base, daily_usd=7, monthly_usd=None)
    await _put(client, o, mine, monthly_usd=20)
    body = (await client.get(base, headers=m)).json()
    assert [(b["scope"], b["period"], b["limit_usd"]) for b in body["budgets"]] == [
        ("org", "day", 7.0),
        ("assistant", "month", 20.0),
    ]
    assert (await client.put(base, json={"daily_usd": 0}, headers=o)).status_code == 422

    async with get_sessionmaker()() as s:
        rows = (
            await s.scalars(
                select(AuditLog)
                .where(AuditLog.action == "budget.updated")
                .order_by(AuditLog.created_at)
            )
        ).all()
    assert [r.meta for r in rows] == [
        {"day": {"from": None, "to": 5}, "month": {"from": None, "to": 50}},
        {"day": {"from": 5.0, "to": 7}, "month": {"from": 50.0, "to": None}},
        {"month": {"from": None, "to": 20}},
    ]
    assert rows[-1].target_type == "assistant" and rows[-1].target_id == aid


# ── in chat ──────────────────────────────────────────────────


async def _chat(client: AsyncClient, headers: dict[str, str], aid: str, text: str) -> list[Any]:
    r = await client.post(f"/api/v1/assistants/{aid}/conversations", json={}, headers=headers)
    cid = r.json()["id"]
    events: list[Any] = []
    async with client.stream(
        "POST", f"/api/v1/conversations/{cid}/messages", json={"text": text}, headers=headers
    ) as resp:
        async for line in resp.aiter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    events.append({"type": "_conversation", "id": cid})
    return events


async def test_at_100_percent_the_turn_is_refused_before_it_runs(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    org = org_headers["X-Org-Id"]
    aid = await _assistant(client, org_headers)
    await _put(client, org_headers, f"/api/v1/orgs/{org}/budgets", daily_usd=1)
    await _spend(org, 1.0)
    events = await _chat(client, org_headers, aid, "hello")
    assert [e["type"] for e in events] == ["error", "_conversation"]
    assert events[0]["code"] == "budget_exceeded"
    assert events[0]["message"] == (
        "This organisation's daily budget of $1.00 is used up. It resets at midnight UTC."
    )
    async with get_sessionmaker()() as s:
        saved = await s.scalars(
            select(Message).where(Message.conversation_id == uuid.UUID(events[-1]["id"]))
        )
        assert saved.all() == [], "nothing ran, nothing was saved"


async def test_from_80_percent_the_turn_says_so_and_runs(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    org = org_headers["X-Org-Id"]
    aid = await _assistant(client, org_headers)
    await _put(client, org_headers, f"/api/v1/assistants/{aid}/budget", monthly_usd=1)
    await _spend(org, 0.85, assistant_id=aid)
    await _spend(org, 5.0)  # another assistant's spend: not this budget's
    events = await _chat(client, org_headers, aid, "hello")
    assert events[0] == {
        "type": "budget",
        "scope": "assistant",
        "period": "month",
        "ratio": pytest.approx(0.85),
        "message": "This assistant's monthly budget is 85% used ($0.85 of $1.00).",
    }
    assert any(e["type"] == "done" for e in events)
    assert not any(e["type"] == "error" for e in events)


async def test_a_turn_stops_mid_way_at_the_tightest_budget(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    org = org_headers["X-Org-Id"]
    aid = await _assistant(client, org_headers)
    await _put(client, org_headers, f"/api/v1/assistants/{aid}/budget", daily_usd=0.01)
    await _spend(org, 0.00999, assistant_id=aid)
    events = await _chat(client, org_headers, aid, "spend what is left and then some")
    errors = [e for e in events if e["type"] == "error"]
    assert [e["code"] for e in errors] == ["budget_exceeded"]
    assert errors[0]["message"].startswith("This assistant's daily budget of $0.01 is used up.")


# ── the other things that spend ──────────────────────────────


async def test_the_ai_helpers_stop_on_the_real_model_but_not_on_the_template(
    client: AsyncClient, org_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    org = org_headers["X-Org-Id"]
    aid = await _assistant(client, org_headers)
    await _put(client, org_headers, f"/api/v1/orgs/{org}/budgets", monthly_usd=2)
    await _spend(org, 2.0)
    body = {"description": "Answers questions about our returns policy."}

    monkeypatch.setattr(claude_api, "real_model_allowed", lambda: False)
    url = f"/api/v1/assistants/{aid}/prompt:generate"
    assert (await client.post(url, json=body, headers=org_headers)).status_code == 200

    monkeypatch.setattr(claude_api, "real_model_allowed", lambda: True)
    stub = claude_stub.install(monkeypatch, lambda r: pytest.fail("no call over budget"))
    for path in (url, f"/api/v1/assistants/{aid}/pipeline:recommend", "/api/v1/pipeline:recommend"):
        r = await client.post(path, json=body, headers=org_headers)
        assert r.status_code == 402, (path, r.text)
        assert r.json()["error"]["code"] == "budget_exceeded"
    assert stub.requests == []


async def test_paid_contextual_retrieval_waits_for_budget(
    client: AsyncClient, org_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    org = org_headers["X-Org-Id"]
    aid = await _assistant(client, org_headers)
    await _put(client, org_headers, f"/api/v1/assistants/{aid}/budget", daily_usd=0.05)
    await _spend(org, 0.03, assistant_id=aid)

    class Paid:
        enabled = True
        name = "paid"

        def estimate_cost(self, _doc: str, _chunks: list[str]) -> float:
            return 0.04

        async def contextualize(self, *_a: Any) -> Any:
            raise AssertionError("over budget: must not run")

    monkeypatch.setattr(ingest, "get_contextualizer", lambda _on: Paid())
    report = ingest.IngestReport()
    async with get_sessionmaker()() as s:
        a = await s.get_one(Assistant, uuid.UUID(aid))
        prefixes, spent = await ingest._context_prefixes(
            s,
            SimpleNamespace(id=uuid.uuid4(), org_id=a.org_id, assistant_id=a.id),  # type: ignore[arg-type]
            SimpleNamespace(parsed=SimpleNamespace(text="doc"), checksum="x"),  # type: ignore[arg-type]
            [SimpleNamespace(ordinal=0, text="chunk")],  # type: ignore[list-item]
            SimpleNamespace(contextual_retrieval=True),  # type: ignore[arg-type]
            {},
            report,
        )
    assert (prefixes, spent) == ([None], None)
    assert report.context_skipped == (
        "estimated $0.04 for 1 chunks is more than the $0.02 left in the assistant's daily budget"
    )


async def test_an_assistants_budgets_go_with_it(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    org = org_headers["X-Org-Id"]
    aid = await _assistant(client, org_headers)
    await _put(client, org_headers, f"/api/v1/assistants/{aid}/budget", daily_usd=3)
    assert (
        await client.delete(f"/api/v1/assistants/{aid}", headers=org_headers)
    ).status_code == 200
    async with get_sessionmaker()() as s:
        assert (await budgets.list_for_org(s, uuid.UUID(org))) == []
