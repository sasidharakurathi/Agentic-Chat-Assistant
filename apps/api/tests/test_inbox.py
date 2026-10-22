"""The approvals inbox, emails that only link, and My requests (Phase 9.2).

One page per request, where every email links: its asker, whoever may
decide it or decided it, and admins see it (an employee who only asked
sees it as Chat shows it); anyone else in the org is refused. The inbox's
views: waiting for me, decided by me, and (admins) every request. A burst
of requests is one email per approver, not one each.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from app.db.session import get_sessionmaker
from app.mail import backends
from app.models.approval import ApproverNotice
from app.services import approvals as approvals_svc
from httpx import AsyncClient
from sqlalchemy import select
from tests.pending import decide
from tests.test_chat_approvals import fake_sql
from tests.test_requests import Org, _ask, _live, _org

__all__ = ["fake_sql"]  # a fixture, imported for use by name

pytestmark = pytest.mark.anyio

API = "/api/v1"


@pytest.fixture(autouse=True)
def _empty_outbox() -> Any:
    backends.outbox.clear()
    yield
    backends.outbox.clear()


@pytest.fixture
async def org(client: AsyncClient) -> Org:
    return await _org(client)


def _sent(kind: str) -> list[Any]:
    return [m for m in backends.outbox if m.kind == kind]


# ── one request's page ───────────────────────────────────────


async def test_each_person_sees_a_request_as_they_may(
    client: AsyncClient, org: Org, fake_sql: dict[str, Any]
) -> None:
    aid = await _live(client, org)
    _, rid = await _ask(client, org, aid)
    url = f"{API}/approvals/{rid}"

    mine = (await client.get(url, headers=org.h("pat"))).json()
    assert (mine["status"], mine["can_decide"], mine["can_withdraw"]) == ("pending", False, True)
    assert mine["rationale"] is None and mine["input"] == {}, "Pat sees it as Chat shows it"

    theirs = (await client.get(url, headers=org.h("admin"))).json()
    assert theirs["can_decide"] is True and theirs["can_withdraw"] is False
    assert theirs["rationale"].startswith("UPDATE laptops")
    assert (theirs["asker_email"], theirs["assistant_name"]) == ("pat@example.com", "DB Bot")

    for who in ("sam", "builder"):
        r = await client.get(url, headers=org.h(who))
        assert (r.status_code, r.json()["error"]["code"]) == (403, "not_your_request"), who

    outsider = await client.post(
        f"{API}/auth/register",
        json={"email": "elsewhere@example.com", "password": "supersecret", "name": "x"},
    )
    h = {"Authorization": f"Bearer {outsider.json()['access_token']}"}
    assert (await client.get(url, headers=h)).status_code == 404

    assert (await decide(client, org.h("admin"), rid, "denied")).status_code == 200
    after = (await client.get(url, headers=org.h("admin"))).json()
    assert (after["status"], after["can_decide"], after["decided_by_name"]) == (
        "denied",
        False,
        "admin",
    )
    assert (await client.get(url, headers=org.h("pat"))).json()["note"] == "Not this one."


# ── the inbox's views ────────────────────────────────────────


async def test_my_requests_decided_by_me_and_the_whole_organisation(
    client: AsyncClient, org: Org, fake_sql: dict[str, Any]
) -> None:
    aid = await _live(client, org)
    _, first = await _ask(client, org, aid)
    # SQLite's timestamps are whole seconds: newest-first needs them apart.
    await asyncio.sleep(1.1)
    _, second = await _ask(client, org, aid)
    _, sams = await _ask(client, org, aid, who="sam")
    await decide(client, org.h("admin"), first, "denied")

    mine = (await client.get(f"{API}/approvals/mine", headers=org.h("pat"))).json()["items"]
    assert [r["id"] for r in mine] == [second, first], "Pat's own, newest first"
    assert all(r["rationale"] is None for r in mine)
    assert [r["can_withdraw"] for r in mine] == [True, False]

    decided = (await client.get(f"{API}/approvals/decided", headers=org.h("admin"))).json()
    assert [r["id"] for r in decided["items"]] == [first]
    assert (await client.get(f"{API}/approvals/decided", headers=org.h("owner"))).json()[
        "items"
    ] == []

    url = f"{API}/orgs/{org.id}/approvals"
    every = (await client.get(url, headers=org.h("admin"))).json()["items"]
    assert [r["id"] for r in every] == [sams, second, first]
    denied = (await client.get(f"{url}?status=denied", headers=org.h("owner"))).json()["items"]
    assert [r["id"] for r in denied] == [first]
    other = uuid.uuid4()
    none = (await client.get(f"{url}?assistant_id={other}", headers=org.h("owner"))).json()
    assert none["items"] == []
    refused = await client.get(url, headers=org.h("builder"))
    assert refused.status_code == 403


async def test_the_counts_in_the_navigation(
    client: AsyncClient, org: Org, fake_sql: dict[str, Any]
) -> None:
    aid = await _live(client, org)
    _, first = await _ask(client, org, aid)
    await _ask(client, org, aid)
    await _ask(client, org, aid)
    # Decided: neither waiting for anyone nor open any more.
    await decide(client, org.h("admin"), first, "denied")
    url = f"{API}/approvals/summary"
    assert (await client.get(url, headers=org.h("admin"))).json() == {
        "to_decide": 2,
        "mine_open": 0,
    }
    assert (await client.get(url, headers=org.h("pat"))).json() == {"to_decide": 0, "mine_open": 2}
    assert (await client.get(url, headers=org.h("builder"))).json() == {
        "to_decide": 0,
        "mine_open": 0,
    }


# ── emails ───────────────────────────────────────────────────


async def test_emails_link_to_the_request_and_decide_nothing(
    client: AsyncClient, org: Org, fake_sql: dict[str, Any]
) -> None:
    aid = await _live(client, org)
    _, rid = await _ask(client, org, aid)
    waiting = _sent("approval_waiting")
    assert waiting and all(f"/approvals/{rid}" in m.text for m in waiting)
    assert all("UPDATE" not in m.text for m in waiting), "what it would do isn't in it"
    await decide(client, org.h("admin"), rid, "denied")
    (told,) = _sent("approval_outcome")
    assert told.to == "pat@example.com" and f"/approvals/{rid}" in told.text


async def test_a_burst_of_requests_is_one_email_each(
    client: AsyncClient, org: Org, fake_sql: dict[str, Any]
) -> None:
    aid = await _live(client, org)
    await _ask(client, org, aid)
    assert sorted(m.to for m in _sent("approval_waiting")) == [
        "admin@example.com",
        "owner@example.com",
    ]
    backends.outbox.clear()
    await _ask(client, org, aid)
    await _ask(client, org, aid, who="sam")
    assert _sent("approval_waiting") == [], "inside the quiet window: held back"

    await approvals_svc.sweep()
    assert _sent("approvals_waiting") == [], "not before the window has passed"

    async with get_sessionmaker()() as s:
        for notice in (await s.scalars(select(ApproverNotice))).all():
            notice.last_sent_at = datetime.now(UTC) - timedelta(minutes=16)
        await s.commit()
    counts = await approvals_svc.sweep()
    assert counts["batched"] == 2
    batch = _sent("approvals_waiting")
    assert sorted(m.to for m in batch) == ["admin@example.com", "owner@example.com"]
    assert all("3 requests wait" in m.subject for m in batch)
    assert (await approvals_svc.sweep())["batched"] == 0, "once"
