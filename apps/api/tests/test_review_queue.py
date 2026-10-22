"""The needs-review queue (Phase 10.3).

- **What lands in it**: answers in live conversations that got a thumbs
  down, or whose conversation "Did this solve it?" said no: what the person
  chose to send. A failed turn is noted on such an item, never listed by
  itself. Not a thumbs up, not a builder's preview.
- **Who sees it**: the assistant's builders, with the question, answer and
  comment, never who asked; an admin also gets the conversation.
- **What a builder does**: turn it into a test case (which remembers the
  answer it came from), or dismiss it with a reason. Each decision once,
  and recorded.
- **Told when it is fixed**: once the case passes in the newest finished
  run on the live version, the asker gets one email, with no conversation
  content in it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from app.db.session import get_sessionmaker
from app.mail import backends
from app.models.assistant import Assistant
from app.models.audit_log import AuditLog
from app.models.conversation import Conversation, Run, RunStatus
from app.models.evals import EvalCase, EvalCaseResult, EvalRun, EvalRunStatus, EvalSuite
from app.models.review import ReviewItem
from app.services import review_queue
from httpx import AsyncClient
from sqlalchemy import select
from tests.versions import go_live

from test_audiences import Org, _org  # type: ignore[import-not-found]
from test_history_feedback import _chat, _live, _rate  # type: ignore[import-not-found]

pytestmark = pytest.mark.anyio

API = "/api/v1"


@pytest.fixture
async def org(client: AsyncClient) -> Org:
    return await _org(client)


@pytest.fixture(autouse=True)
def _empty_outbox() -> Any:
    backends.outbox.clear()
    yield
    backends.outbox.clear()


async def _queue(
    client: AsyncClient, org: Org, aid: str, who: str = "builder", state: str = "open"
) -> list[dict[str, Any]]:
    r = await client.get(
        f"{API}/assistants/{aid}/review-queue", params={"state": state}, headers=org.h(who)
    )
    assert r.status_code == 200, r.text
    return list(r.json())


async def _suite(client: AsyncClient, org: Org, aid: str, name: str = "From Chat") -> str:
    r = await client.post(
        f"{API}/assistants/{aid}/eval-suites", json={"name": name}, headers=org.h("builder")
    )
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def _failed(cid: str, mid: str) -> None:
    """The turn that wrote this answer ended in an error."""
    async with get_sessionmaker()() as s:
        conv = await s.get(Conversation, uuid.UUID(cid))
        assert conv is not None
        s.add(
            Run(
                conversation_id=conv.id,
                org_id=conv.org_id,
                message_id=uuid.UUID(mid),
                status=RunStatus.error,
                error="The model was unavailable",
            )
        )
        await s.commit()


# ── what lands in the queue, and who sees it ─────────────────


async def test_complaints_land_in_the_queue_without_who_asked(
    client: AsyncClient, org: Org
) -> None:
    aid = await _live(client, org)
    cid1, wifi = await _chat(client, org, "emp1", aid, "Where is the Wi-Fi password?")
    await _rate(client, org, "emp1", cid1, wifi, rating="down", comment="It didn't know.")
    cid2, vpn = await _chat(client, org, "emp2", aid, "How do I set up the VPN?")
    await client.put(
        f"{API}/conversations/{cid2}/outcome", json={"solved": False}, headers=org.h("emp2")
    )
    cid3, liked = await _chat(client, org, "emp1", aid, "Printer on floor 2?")
    await _rate(client, org, "emp1", cid3, liked, rating="up")
    # A failed turn nobody reported isn't theirs to see; one reported says so.
    cid4, broken = await _chat(client, org, "emp2", aid, "Book a meeting room")
    await _failed(cid4, broken)
    cid5, reported = await _chat(client, org, "emp1", aid, "Order a new mouse")
    await _failed(cid5, reported)
    await _rate(client, org, "emp1", cid5, reported, rating="down")
    # A preview is the builder's own testing: not in the queue.
    pcid, pmid = await _chat(client, org, "builder", aid, "test", mode="preview")
    await _rate(client, org, "builder", pcid, pmid, rating="down", comment="draft test")

    items = await _queue(client, org, aid)
    by_id = {i["id"]: i for i in items}
    assert set(by_id) == {wifi, vpn, reported}
    assert by_id[wifi]["reasons"] == ["thumbs_down"]
    assert by_id[wifi]["comment"] == "It didn't know."
    assert by_id[wifi]["question"] == "Where is the Wi-Fi password?"
    assert by_id[wifi]["answer"] and by_id[wifi]["state"] == "open"
    assert by_id[wifi]["version_number"] == 1
    assert by_id[vpn]["reasons"] == ["not_solved"]
    assert by_id[reported]["reasons"] == ["failed", "thumbs_down"]
    assert by_id[reported]["comment"] == ""
    assert all(i["conversation_id"] is None for i in items)

    raw = (await client.get(f"{API}/assistants/{aid}/review-queue", headers=org.h("builder"))).text
    assert "emp1" not in raw and "emp2" not in raw and "user_id" not in raw

    admin = {i["id"]: i for i in await _queue(client, org, aid, who="owner")}
    assert admin[wifi]["conversation_id"] == cid1

    for who in ("emp1", "builder2"):
        r = await client.get(f"{API}/assistants/{aid}/review-queue", headers=org.h(who))
        assert r.status_code == 403, who


# ── turning into a test case, dismissing ─────────────────────


async def test_a_builder_turns_one_into_a_test_case_and_dismisses_another(
    client: AsyncClient, org: Org
) -> None:
    aid = await _live(client, org)
    cid1, wifi = await _chat(client, org, "emp1", aid, "Where is the Wi-Fi password?")
    await _rate(client, org, "emp1", cid1, wifi, rating="down", comment="Wrong floor.")
    cid2, vpn = await _chat(client, org, "emp2", aid, "VPN?")
    await _rate(client, org, "emp2", cid2, vpn, rating="down")
    cid3, liked = await _chat(client, org, "emp1", aid, "Lunch menu?")
    await _rate(client, org, "emp1", cid3, liked, rating="up")
    sid = await _suite(client, org, aid)
    base = f"{API}/assistants/{aid}/review-queue"

    r = await client.post(
        f"{base}/{wifi}:to-case",
        json={"suite_id": sid, "expected": {"contains": ["Wi-Fi"]}},
        headers=org.h("builder"),
    )
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["state"] == "turned_into_case" and got["suite_id"] == sid
    assert got["eval_case_id"] and got["asker_told"] is False
    async with get_sessionmaker()() as s:
        case = await s.get(EvalCase, uuid.UUID(got["eval_case_id"]))
        audit = await s.scalar(select(AuditLog).where(AuditLog.action == "review_item.to_case"))
    assert case is not None and case.input == "Where is the Wi-Fi password?"
    assert case.meta["source_message_id"] == wifi
    assert case.expected["contains"] == ["Wi-Fi"]
    assert audit is not None and audit.meta["suite_id"] == sid
    assert "Wi-Fi" not in str(audit.meta)

    # Decided once: out of the open list, into its own.
    assert [i["id"] for i in await _queue(client, org, aid)] == [vpn]
    (case_item,) = await _queue(client, org, aid, state="turned_into_case")
    assert case_item["id"] == wifi and case_item["comment"] == "Wrong floor."
    again = await client.post(
        f"{base}/{wifi}:to-case", json={"suite_id": sid}, headers=org.h("builder")
    )
    assert (again.status_code, again.json()["error"]["code"]) == (409, "review_item_decided")

    short = await client.post(f"{base}/{vpn}:dismiss", json={"reason": "x"}, headers=org.h("owner"))
    assert short.status_code == 422
    gone = await client.post(
        f"{base}/{vpn}:dismiss",
        json={"reason": "Asked before the VPN page existed"},
        headers=org.h("owner"),
    )
    assert gone.status_code == 200, gone.text
    assert gone.json()["state"] == "dismissed"
    assert gone.json()["dismissed_reason"] == "Asked before the VPN page existed"
    (dismissed,) = await _queue(client, org, aid, state="dismissed")
    assert dismissed["id"] == vpn
    twice = await client.post(
        f"{base}/{vpn}:dismiss", json={"reason": "Still nothing"}, headers=org.h("builder")
    )
    assert twice.status_code == 409
    async with get_sessionmaker()() as s:
        row = await s.scalar(select(AuditLog).where(AuditLog.action == "review_item.dismiss"))
    assert row is not None and "VPN" not in str(row.meta)
    assert await _queue(client, org, aid) == []


async def test_actions_refuse_what_isnt_theirs_or_isnt_in_the_queue(
    client: AsyncClient, org: Org
) -> None:
    aid = await _live(client, org)
    other = await _live(client, org, name="HR")
    cid, wifi = await _chat(client, org, "emp1", aid, "Wi-Fi?")
    await _rate(client, org, "emp1", cid, wifi, rating="down")
    cid2, liked = await _chat(client, org, "emp1", aid, "Lunch?")
    await _rate(client, org, "emp1", cid2, liked, rating="up")
    sid = await _suite(client, org, aid)
    hr_suite = await _suite(client, org, other, name="HR cases")
    base = f"{API}/assistants/{aid}/review-queue"

    wrong_suite = await client.post(
        f"{base}/{wifi}:to-case", json={"suite_id": hr_suite}, headers=org.h("builder")
    )
    assert (wrong_suite.status_code, wrong_suite.json()["error"]["code"]) == (
        404,
        "suite_not_found",
    )
    not_queued = await client.post(
        f"{base}/{liked}:to-case", json={"suite_id": sid}, headers=org.h("builder")
    )
    assert (not_queued.status_code, not_queued.json()["error"]["code"]) == (
        404,
        "review_item_not_found",
    )
    elsewhere = await client.post(
        f"{API}/assistants/{other}/review-queue/{wifi}:dismiss",
        json={"reason": "Not ours"},
        headers=org.h("builder"),
    )
    assert elsewhere.status_code == 404
    for who in ("emp1", "builder2"):
        r = await client.post(f"{base}/{wifi}:to-case", json={"suite_id": sid}, headers=org.h(who))
        assert r.status_code == 403, who


# ── told when it is fixed ────────────────────────────────────


async def _result(
    item_case: str, *, version_id: uuid.UUID | None, number: int | None, ok: bool
) -> None:
    """A finished run of the case's suite with this outcome for the case."""
    async with get_sessionmaker()() as s:
        case = await s.get(EvalCase, uuid.UUID(item_case))
        assert case is not None
        suite = await s.get(EvalSuite, case.suite_id)
        assert suite is not None
        now = datetime.now(UTC)
        run = EvalRun(
            suite_id=suite.id,
            assistant_id=suite.assistant_id,
            org_id=suite.org_id,
            assistant_version_id=version_id,
            version_number=number,
            status=EvalRunStatus.done,
            total_cases=1,
            done_cases=1,
            started_at=now,
            finished_at=now,
        )
        s.add(run)
        await s.flush()
        s.add(
            EvalCaseResult(
                eval_run_id=run.id,
                eval_case_id=case.id,
                org_id=suite.org_id,
                input=case.input,
                expected=case.expected,
                output="An answer",
                passed=ok,
            )
        )
        await s.commit()


async def _live_version(aid: str) -> uuid.UUID:
    async with get_sessionmaker()() as s:
        a = await s.get(Assistant, uuid.UUID(aid))
        assert a is not None and a.current_version_id is not None
        return a.current_version_id


async def _sweep() -> int:
    async with get_sessionmaker()() as s:
        return await review_queue.notify_fixed(s)


async def test_the_asker_is_told_once_when_the_case_passes_on_the_live_version(
    client: AsyncClient, org: Org
) -> None:
    aid = await _live(client, org)
    question = "Where is the Wi-Fi password?"
    cid, wifi = await _chat(client, org, "emp1", aid, question)
    await _rate(client, org, "emp1", cid, wifi, rating="down", comment="It didn't know.")
    sid = await _suite(client, org, aid)
    made = await client.post(
        f"{API}/assistants/{aid}/review-queue/{wifi}:to-case",
        json={"suite_id": sid},
        headers=org.h("builder"),
    )
    case_id = made.json()["eval_case_id"]

    # Still failing on the live version, or passing only on the draft: not yet.
    await _result(case_id, version_id=await _live_version(aid), number=1, ok=False)
    await _result(case_id, version_id=None, number=None, ok=True)
    assert await _sweep() == 0
    assert [m for m in backends.outbox if m.kind == "reported_fixed"] == []

    # The fix is approved as version 2, and the case passes on it.
    number = await go_live(client, aid, org.h("builder"), approver=org.h("owner"))
    assert number == 2
    await _result(case_id, version_id=await _live_version(aid), number=2, ok=True)
    assert await _sweep() == 1
    (sent,) = [m for m in backends.outbox if m.kind == "reported_fixed"]
    assert sent.to == "emp1@example.com"
    assert "version 2" in sent.text and f"/assistants/{aid}/chat" in sent.text
    assert question not in sent.text and "didn't know" not in sent.text

    # Once.
    assert await _sweep() == 0
    assert len([m for m in backends.outbox if m.kind == "reported_fixed"]) == 1
    (item,) = await _queue(client, org, aid, state="turned_into_case")
    assert item["fixed_version_number"] == 2 and item["asker_told"] is True


async def test_nobody_is_told_once_the_conversation_is_erased(
    client: AsyncClient, org: Org
) -> None:
    aid = await _live(client, org)
    cid, wifi = await _chat(client, org, "emp1", aid, "Wi-Fi?")
    await _rate(client, org, "emp1", cid, wifi, rating="down")
    sid = await _suite(client, org, aid)
    made = await client.post(
        f"{API}/assistants/{aid}/review-queue/{wifi}:to-case",
        json={"suite_id": sid, "input": "What is the guest Wi-Fi password?"},
        headers=org.h("builder"),
    )
    assert made.status_code == 200, made.text
    case_id = made.json()["eval_case_id"]
    async with get_sessionmaker()() as s:
        case = await s.get(EvalCase, uuid.UUID(case_id))
        assert case is not None and case.input == "What is the guest Wi-Fi password?"
    await _result(case_id, version_id=await _live_version(aid), number=1, ok=True)

    # The conversation is erased first: its decision goes with it.
    erased = await client.post(
        f"{API}/orgs/{org.id}/members/{org.people['emp1'].user_id}:erase",
        json={"confirm_email": "emp1@example.com"},
        headers=org.h("owner"),
    )
    assert erased.status_code == 200, erased.text
    async with get_sessionmaker()() as s:
        assert list(await s.scalars(select(ReviewItem))) == []
    assert await _sweep() == 0
    assert [m for m in backends.outbox if m.kind == "reported_fixed"] == []
