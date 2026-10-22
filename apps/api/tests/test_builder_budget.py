"""Builder testing spends from its own budget (Phase 9.7).

- Previews and eval runs never count toward the org's or an assistant's
  budgets, so a week of testing can't stop Chat.
- They count toward the builder budget instead: a monthly, org-wide cap
  the owner sets on the Members page, under Organisation (none by default). Reaching it
  stops previews and eval runs with a clear message; Chat carries on.
- The usage page shows builder testing's spend this month.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from app import queue
from app.db.session import get_sessionmaker
from app.evals import runner
from app.models.alert import AdminAlert
from app.models.audit_log import AuditLog
from app.models.conversation import Conversation
from app.models.usage import UsageEvent, UsageKind
from app.services import budgets
from httpx import AsyncClient
from sqlalchemy import func, select
from tests.versions import go_live

pytestmark = pytest.mark.anyio

API = "/api/v1"
CAPPED = (
    "Builder testing has used this month's $1.00. "
    "The owner can raise it in Members › Organisation."  # noqa: RUF001
)


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[uuid.UUID]:
    """Eval runs are queued to a list, and run by the test itself."""
    seen: list[uuid.UUID] = []

    async def enqueue(run_id: uuid.UUID) -> bool:
        seen.append(run_id)
        return True

    monkeypatch.setattr(queue, "enqueue_eval", enqueue)
    return seen


async def _live_assistant(client: AsyncClient, headers: dict[str, str]) -> str:
    r = await client.post(f"{API}/assistants", json={"name": "Helpdesk"}, headers=headers)
    assert r.status_code == 201, r.text
    aid = str(r.json()["id"])
    await go_live(client, aid, headers)
    return aid


async def _conversation(
    client: AsyncClient, headers: dict[str, str], aid: str, *, preview: bool
) -> str:
    body = {"mode": "preview"} if preview else {}
    r = await client.post(f"{API}/assistants/{aid}/conversations", json=body, headers=headers)
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def _ask(client: AsyncClient, headers: dict[str, str], cid: str) -> list[dict[str, Any]]:
    r = await client.post(
        f"{API}/conversations/{cid}/messages", json={"text": "hello"}, headers=headers
    )
    assert r.status_code == 200, r.text
    return [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]


async def _spend(org: str, usd: float, *, conversation_id: str | None = None) -> None:
    """A ledger row: under a conversation, or the org's own (no conversation)."""
    async with get_sessionmaker()() as s:
        s.add(
            UsageEvent(
                org_id=uuid.UUID(org),
                conversation_id=uuid.UUID(conversation_id) if conversation_id else None,
                kind=UsageKind.llm,
                model="claude-sonnet-5",
                cost_usd=usd,
            )
        )
        await s.commit()


async def _set_builder_cap(client: AsyncClient, headers: dict[str, str], usd: float | None) -> Any:
    org = headers["X-Org-Id"]
    r = await client.patch(
        f"{API}/orgs/{org}/settings", json={"builder_monthly_usd": usd}, headers=headers
    )
    assert r.status_code == 200, r.text
    return r.json()


def _done(events: list[dict[str, Any]]) -> bool:
    return any(e["type"] == "done" for e in events) and not any(
        e["type"] == "error" for e in events
    )


# ── kept apart from the pilot's budgets ──────────────────────


async def test_preview_spend_does_not_block_live_chat(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    org = org_headers["X-Org-Id"]
    aid = await _live_assistant(client, org_headers)
    r = await client.put(f"{API}/orgs/{org}/budgets", json={"daily_usd": 1}, headers=org_headers)
    assert r.status_code == 200, r.text
    preview = await _conversation(client, org_headers, aid, preview=True)
    # Five times the org's budget, spent on testing.
    await _spend(org, 5.0, conversation_id=preview)

    live = await _conversation(client, org_headers, aid, preview=False)
    assert _done(await _ask(client, org_headers, live)), "Chat isn't stopped by testing"

    listing = (await client.get(f"{API}/orgs/{org}/budgets", headers=org_headers)).json()
    (day,) = [b for b in listing["budgets"] if b["scope"] == "org"]
    assert day["spent_usd"] < 1, "the org's budget counts only live spend"
    assert listing["builder"]["spent_usd"] == pytest.approx(5.0)
    assert listing["builder"]["limit_usd"] is None, "no cap until the owner sets one"


async def test_previews_carry_on_when_the_pilots_budget_is_used_up(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    org = org_headers["X-Org-Id"]
    aid = await _live_assistant(client, org_headers)
    r = await client.put(f"{API}/orgs/{org}/budgets", json={"daily_usd": 1}, headers=org_headers)
    assert r.status_code == 200, r.text
    await _spend(org, 1.0)  # the pilot's

    live = await _ask(
        client, org_headers, await _conversation(client, org_headers, aid, preview=False)
    )
    assert live[0]["code"] == "budget_exceeded"
    preview = await _conversation(client, org_headers, aid, preview=True)
    assert _done(await _ask(client, org_headers, preview)), "testing isn't the pilot's"


# ── the builder cap ──────────────────────────────────────────


async def test_the_builder_cap_stops_previews_but_not_live_chat(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    org = org_headers["X-Org-Id"]
    aid = await _live_assistant(client, org_headers)
    settings = await _set_builder_cap(client, org_headers, 1)
    assert settings["builder_monthly_usd"] == 1
    preview = await _conversation(client, org_headers, aid, preview=True)
    await _spend(org, 1.0, conversation_id=preview)

    events = await _ask(client, org_headers, preview)
    assert [e["type"] for e in events] == ["error"]
    assert events[0]["code"] == "builder_budget"
    assert events[0]["message"] == CAPPED
    # A new preview is stopped too: the cap is org-wide.
    again = await _ask(
        client, org_headers, await _conversation(client, org_headers, aid, preview=True)
    )
    assert again[0]["code"] == "builder_budget"

    live = await _conversation(client, org_headers, aid, preview=False)
    assert _done(await _ask(client, org_headers, live)), "Chat keeps working"
    async with get_sessionmaker()() as s:
        assert list(await s.scalars(select(AdminAlert))) == [], "testing stopping alerts no one"
        audited = await s.scalar(select(AuditLog).where(AuditLog.action == "org.settings.update"))
    assert audited is not None
    assert audited.meta["changed"]["builder_monthly_usd"] == {"from": None, "to": 1}

    listing = (await client.get(f"{API}/orgs/{org}/budgets", headers=org_headers)).json()
    assert listing["builder"]["limit_usd"] == 1
    assert listing["builder"]["state"] == "exceeded"


async def test_the_builder_cap_warns_from_80_percent(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    org = org_headers["X-Org-Id"]
    aid = await _live_assistant(client, org_headers)
    await _set_builder_cap(client, org_headers, 1)
    preview = await _conversation(client, org_headers, aid, preview=True)
    await _spend(org, 0.85, conversation_id=preview)
    events = await _ask(client, org_headers, preview)
    (note,) = [e for e in events if e["type"] == "budget"]
    assert note["scope"] == "builder" and note["period"] == "month"
    assert note["message"] == "Builder testing is 85% used this month ($0.85 of $1.00)."
    assert any(e["type"] == "done" for e in events)


async def test_the_cap_is_at_least_a_cent_and_null_removes_it(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    org = org_headers["X-Org-Id"]
    url = f"{API}/orgs/{org}/settings"
    bad = await client.patch(url, json={"builder_monthly_usd": 0}, headers=org_headers)
    assert bad.status_code == 422, "at least a cent, or none"
    await _set_builder_cap(client, org_headers, 25)
    # Another change leaves it as it was.
    r = await client.patch(url, json={"review_emails": False}, headers=org_headers)
    assert r.json()["builder_monthly_usd"] == 25
    cleared = await _set_builder_cap(client, org_headers, None)
    assert cleared["builder_monthly_usd"] is None
    async with get_sessionmaker()() as s:
        assert await budgets.builder_status(s, uuid.UUID(org)) is None


# ── eval runs ────────────────────────────────────────────────


async def test_eval_runs_count_as_builder_spend(
    client: AsyncClient, org_headers: dict[str, str], queued: list[uuid.UUID]
) -> None:
    org = org_headers["X-Org-Id"]
    aid = await _live_assistant(client, org_headers)
    r = await client.put(
        f"{API}/orgs/{org}/budgets", json={"monthly_usd": 100}, headers=org_headers
    )
    assert r.status_code == 200, r.text
    suite = await client.post(
        f"{API}/assistants/{aid}/eval-suites",
        json={"name": "Basics", "config": {"judge": False}},
        headers=org_headers,
    )
    sid = suite.json()["id"]
    await client.post(
        f"{API}/eval-suites/{sid}/cases:bulk",
        json={"cases": [{"input": "hello one"}, {"input": "hello two"}]},
        headers=org_headers,
    )
    started = await client.post(
        f"{API}/eval-suites/{sid}/runs", json={"assistant_version_id": None}, headers=org_headers
    )
    assert started.status_code == 202, started.text
    await runner.run(uuid.UUID(started.json()["id"]))

    async with get_sessionmaker()() as s:
        turns = float(
            await s.scalar(
                select(func.coalesce(func.sum(Conversation.cost_usd), 0)).where(
                    Conversation.eval_run_id.is_not(None)
                )
            )
            or 0
        )
    listing = (await client.get(f"{API}/orgs/{org}/budgets", headers=org_headers)).json()
    (month,) = [b for b in listing["budgets"] if b["scope"] == "org"]
    assert month["spent_usd"] == pytest.approx(0.0), "not the pilot's"
    assert listing["builder"]["spent_usd"] == pytest.approx(turns, abs=1e-6)


async def test_a_spent_builder_budget_stops_an_eval_run_but_not_chat(
    client: AsyncClient, org_headers: dict[str, str], queued: list[uuid.UUID]
) -> None:
    org = org_headers["X-Org-Id"]
    aid = await _live_assistant(client, org_headers)
    await _set_builder_cap(client, org_headers, 1)
    preview = await _conversation(client, org_headers, aid, preview=True)
    await _spend(org, 1.0, conversation_id=preview)
    suite = await client.post(
        f"{API}/assistants/{aid}/eval-suites",
        json={"name": "Basics", "config": {"judge": False}},
        headers=org_headers,
    )
    sid = suite.json()["id"]
    await client.post(
        f"{API}/eval-suites/{sid}/cases:bulk",
        json={"cases": [{"input": "one"}, {"input": "two"}]},
        headers=org_headers,
    )
    started = await client.post(
        f"{API}/eval-suites/{sid}/runs", json={"assistant_version_id": None}, headers=org_headers
    )
    rid = started.json()["id"]
    await runner.run(uuid.UUID(rid))
    run = (await client.get(f"{API}/eval-runs/{rid}", headers=org_headers)).json()
    assert run["status"] == "failed"
    assert run["error"] == CAPPED
    assert len(run["results"]) == 1, "no point asking the second question"

    live = await _conversation(client, org_headers, aid, preview=False)
    assert _done(await _ask(client, org_headers, live))
