"""Analytics per assistant with a baseline, and the managers' report
(Phase 10.1).

- **Live only**: previews, eval conversations and conversations nobody
  asked anything in are not counted.
- **Resolved** only when the person said so; **not resolved** when they
  said no or gave a thumbs down without a later yes; otherwise **no
  verdict**. An outcome kept after its conversation was erased still
  counts.
- **Spend** is the ledger's, for live conversations, in the period; cost
  per resolved conversation divides it by the resolved ones.
- **Baseline**: the same figures for the period before; a daily series.
- **Who**: the assistant's editors and admins; the org report (and its
  CSV, which is recorded) admins only.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.db.session import get_sessionmaker
from app.models.audit_log import AuditLog
from app.models.conversation import (
    Conversation,
    ConversationMode,
    Message,
    MessageRole,
    Run,
    RunStatus,
)
from app.models.evals import EvalRun, EvalSuite
from app.models.feedback import ConversationOutcome, MessageFeedback
from app.models.usage import UsageEvent, UsageKind
from httpx import AsyncClient
from sqlalchemy import select

from test_audiences import Org, _org  # type: ignore[import-not-found]

pytestmark = pytest.mark.anyio

API = "/api/v1"


@pytest.fixture
async def org(client: AsyncClient) -> Org:
    return await _org(client)


async def _assistant(client: AsyncClient, org: Org, name: str = "Helpdesk") -> str:
    r = await client.post(f"{API}/assistants", json={"name": name}, headers=org.h("builder"))
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def _seed(
    org: Org,
    aid: str,
    who: str,
    at: datetime,
    *,
    mode: ConversationMode = ConversationMode.live,
    eval_run: uuid.UUID | None = None,
    asked: bool = True,
    solved: bool | None = None,
    down: bool = False,
    error: bool = False,
    cost: float = 0.0,
) -> uuid.UUID:
    """One conversation with what happened in it, at `at`."""
    org_id, assistant_id = uuid.UUID(org.id), uuid.UUID(aid)
    user_id = uuid.UUID(org.people[who].user_id)
    async with get_sessionmaker()() as s:
        conv = Conversation(
            assistant_id=assistant_id,
            org_id=org_id,
            created_by=user_id,
            mode=mode,
            eval_run_id=eval_run,
            created_at=at,
            updated_at=at,
            last_message_at=at if asked else None,
        )
        s.add(conv)
        await s.flush()
        answer = Message(
            conversation_id=conv.id,
            org_id=org_id,
            role=MessageRole.assistant,
            content="An answer",
            created_at=at,
        )
        s.add(answer)
        await s.flush()
        if down:
            s.add(
                MessageFeedback(
                    org_id=org_id,
                    assistant_id=assistant_id,
                    conversation_id=conv.id,
                    message_id=answer.id,
                    user_id=user_id,
                    rating="down",
                )
            )
        if solved is not None:
            s.add(
                ConversationOutcome(
                    org_id=org_id,
                    assistant_id=assistant_id,
                    conversation_id=conv.id,
                    version_number=1,
                    solved=solved,
                    created_at=at,
                    updated_at=at,
                )
            )
        if error:
            s.add(
                Run(conversation_id=conv.id, org_id=org_id, status=RunStatus.error, created_at=at)
            )
        if cost:
            s.add(
                UsageEvent(
                    org_id=org_id,
                    assistant_id=assistant_id,
                    conversation_id=conv.id,
                    user_id=user_id,
                    kind=UsageKind.llm,
                    cost_usd=cost,
                    created_at=at,
                )
            )
        await s.commit()
        return conv.id


async def _eval_run(org: Org, aid: str) -> uuid.UUID:
    async with get_sessionmaker()() as s:
        suite = EvalSuite(assistant_id=uuid.UUID(aid), org_id=uuid.UUID(org.id), name="Suite")
        s.add(suite)
        await s.flush()
        run = EvalRun(suite_id=suite.id, assistant_id=uuid.UUID(aid), org_id=uuid.UUID(org.id))
        s.add(run)
        await s.commit()
        return run.id


async def _erased_outcome(org: Org, aid: str, at: datetime, *, solved: bool) -> None:
    """What an erasure leaves (8.10): the outcome, with no conversation."""
    async with get_sessionmaker()() as s:
        s.add(
            ConversationOutcome(
                org_id=uuid.UUID(org.id),
                assistant_id=uuid.UUID(aid),
                conversation_id=None,
                version_number=1,
                solved=solved,
                created_at=at,
                updated_at=at,
            )
        )
        await s.commit()


async def _seed_story(org: Org, aid: str) -> None:
    """A week with five live conversations and an erased one, the week
    before with two, and everything that must not count."""
    now = datetime.now(UTC)
    recent, before, long_ago = (
        now - timedelta(days=1),
        now - timedelta(days=10),
        now - timedelta(days=30),
    )
    # This week.
    await _seed(org, aid, "emp1", recent, solved=True, cost=0.30)
    await _seed(org, aid, "emp1", recent, solved=False, cost=0.10)
    await _seed(org, aid, "emp2", recent, down=True, error=True, cost=0.05)
    # A thumbs down, then "yes, it solved it": resolved.
    await _seed(org, aid, "emp2", recent, down=True, solved=True, cost=0.05)
    await _seed(org, aid, "emp1", recent)
    await _erased_outcome(org, aid, recent, solved=True)
    # Not counted: a builder's preview, an eval case, nobody asked anything.
    await _seed(org, aid, "builder", recent, mode=ConversationMode.preview, solved=True, cost=1.0)
    await _seed(
        org, aid, "owner", recent, eval_run=await _eval_run(org, aid), solved=True, cost=1.0
    )
    await _seed(org, aid, "emp2", recent, asked=False)
    # The week before.
    await _seed(org, aid, "emp1", before, solved=True, cost=0.20)
    await _seed(org, aid, "emp2", before)
    # Outside both periods.
    await _seed(org, aid, "emp1", long_ago, solved=True, cost=5.0)


async def test_figures_with_a_baseline_and_a_daily_series(client: AsyncClient, org: Org) -> None:
    aid = await _assistant(client, org)
    await _seed_story(org, aid)

    r = await client.get(f"{API}/assistants/{aid}/analytics?days=7", headers=org.h("builder"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["days"] == 7
    now = body["current"]
    assert now["conversations"] == 6
    assert now["people"] == 2, "the erased outcome has no person"
    assert (now["resolved"], now["not_resolved"], now["no_verdict"]) == (3, 2, 1)
    assert now["with_errors"] == 1
    assert now["spend_usd"] == pytest.approx(0.50)
    assert now["resolution_rate"] == pytest.approx(0.6)
    assert now["cost_per_resolved_usd"] == pytest.approx(0.50 / 3, abs=1e-6)

    before = body["previous"]
    assert before["conversations"] == 2 and before["people"] == 2
    assert (before["resolved"], before["not_resolved"], before["no_verdict"]) == (1, 0, 1)
    assert before["spend_usd"] == pytest.approx(0.20)
    assert before["resolution_rate"] == pytest.approx(1.0)
    assert before["cost_per_resolved_usd"] == pytest.approx(0.20)

    series = body["series"]
    assert len(series) == 7
    assert series[-1]["date"] == datetime.now(UTC).date().isoformat()
    assert sum(d["conversations"] for d in series) == 6
    assert sum(d["resolved"] for d in series) == 3
    assert sum(d["spend_usd"] for d in series) == pytest.approx(0.50)


async def test_nothing_yet_says_none_rather_than_zero(client: AsyncClient, org: Org) -> None:
    aid = await _assistant(client, org)
    r = await client.get(f"{API}/assistants/{aid}/analytics", headers=org.h("builder"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["days"] == 30 and len(body["series"]) == 30
    assert body["current"]["conversations"] == 0
    assert body["current"]["resolution_rate"] is None
    assert body["current"]["cost_per_resolved_usd"] is None
    bad = await client.get(f"{API}/assistants/{aid}/analytics?days=0", headers=org.h("builder"))
    assert bad.status_code == 422


async def test_only_its_editors_and_admins_read_an_assistants_analytics(
    client: AsyncClient, org: Org
) -> None:
    aid = await _assistant(client, org)
    url = f"{API}/assistants/{aid}/analytics"
    for who in ("builder", "owner"):
        assert (await client.get(url, headers=org.h(who))).status_code == 200, who
    assert (await client.get(url, headers=org.h("builder2"))).status_code == 403
    assert (await client.get(url, headers=org.h("emp1"))).status_code in (403, 404)


async def test_the_managers_report_per_assistant_with_totals(client: AsyncClient, org: Org) -> None:
    aid = await _assistant(client, org)
    idle = await _assistant(client, org, name="Idle")
    await _seed_story(org, aid)
    await _seed(org, idle, "builder2", datetime.now(UTC) - timedelta(days=2), solved=False)

    r = await client.get(f"{API}/orgs/{org.id}/analytics?days=7", headers=org.h("owner"))
    assert r.status_code == 200, r.text
    body = r.json()
    rows = {row["assistant_id"]: row for row in body["assistants"]}
    assert body["assistants"][0]["assistant_id"] == aid, "busiest first"
    helpdesk = rows[aid]
    assert helpdesk["name"] == "Helpdesk"
    assert helpdesk["current"]["conversations"] == 6
    assert helpdesk["current"]["resolution_rate"] == pytest.approx(0.6)
    assert helpdesk["previous"]["conversations"] == 2
    assert rows[idle]["current"]["not_resolved"] == 1
    assert rows[idle]["current"]["cost_per_resolved_usd"] is None
    total = body["total"]
    assert total["conversations"] == 7
    assert total["people"] == 3, "different people across assistants"
    assert total["resolved"] == 3 and total["not_resolved"] == 3
    assert total["spend_usd"] == pytest.approx(0.50)
    assert body["previous_total"]["conversations"] == 2

    refused = await client.get(f"{API}/orgs/{org.id}/analytics", headers=org.h("builder"))
    assert refused.status_code == 403


async def test_the_report_downloads_as_csv_and_the_download_is_recorded(
    client: AsyncClient, org: Org
) -> None:
    aid = await _assistant(client, org)
    await _seed_story(org, aid)
    url = f"{API}/orgs/{org.id}/analytics.csv?days=7"
    assert (await client.get(url, headers=org.h("builder"))).status_code == 403

    r = await client.get(url, headers=org.h("owner"))
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(r.text)))
    assert rows[0][:4] == ["assistant", "status", "conversations", "people"]
    helpdesk = next(row for row in rows if row[0] == "Helpdesk")
    assert helpdesk[2] == "6" and helpdesk[7] == "0.6000"
    assert rows[-1][0] == "Total"

    async with get_sessionmaker()() as s:
        entry = await s.scalar(select(AuditLog).where(AuditLog.action == "analytics.export"))
    assert entry is not None
    assert entry.meta["days"] == 7
