"""The approval gate (Phase 7b.1, plan D4 and D16).

A builder submits a version; it answers nobody until an admin approves it.
Chat answers only from the approved version, never from the draft; the
draft is the builder's, in a preview only they and admins can open. An
admin can reject with a reason, roll back to an earlier approved version,
and pause the assistant, which stops it at once. A builder can no longer
set the status by editing the assistant.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from app.agent.events import TokenEvent, UsageEvent
from app.db.session import get_sessionmaker
from app.models.approval import Approval, ApprovalStatus
from app.models.audit_log import AuditLog
from app.models.conversation import Conversation, ConversationMode, Run
from app.services import assistants as assistants_svc
from app.services import chat as chat_svc
from httpx import AsyncClient, Response
from sqlalchemy import select
from tests.pending import waiting_request
from tests.versions import PREVIEW, submit

from test_rbac_escalation import Team, _team  # type: ignore[import-not-found]

pytestmark = pytest.mark.anyio

API = "/api/v1"


class _PromptSpy:
    """Records the system prompt each turn ran with: which config answered."""

    name = "spy"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def stream(self, *, spec: Any, **_: Any) -> Any:
        self.prompts.append(spec.system_prompt)
        yield TokenEvent(text="ok")
        yield UsageEvent(tokens_in=1, tokens_out=1)


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> _PromptSpy:
    s = _PromptSpy()
    monkeypatch.setattr("app.agent.runtime.get_driver", lambda: s)
    return s


async def _assistant(client: AsyncClient, h: dict[str, str], prompt: str = "Draft one.") -> str:
    a = (await client.post(f"{API}/assistants", json={"name": "Desk"}, headers=h)).json()
    await _prompt(client, h, a["id"], prompt)
    return str(a["id"])


async def _prompt(client: AsyncClient, h: dict[str, str], aid: str, prompt: str) -> None:
    cfg = (await client.get(f"{API}/assistants/{aid}", headers=h)).json()["draft_config"]
    cfg["system_prompt"] = prompt
    r = await client.put(f"{API}/assistants/{aid}/draft-config", json=cfg, headers=h)
    assert r.status_code == 200, r.text


async def _review(
    client: AsyncClient, h: dict[str, str], aid: str, n: int, action: str, **body: Any
) -> Response:
    return await client.post(f"{API}/assistants/{aid}/versions/{n}/{action}", json=body, headers=h)


async def _start(
    client: AsyncClient, h: dict[str, str], aid: str, body: dict[str, Any] | None = None
) -> Response:
    return await client.post(f"{API}/assistants/{aid}/conversations", json=body or {}, headers=h)


async def _turn(cid: str) -> list[Any]:
    async with get_sessionmaker()() as session:
        return [
            e
            async for e in chat_svc.run_message(session, conversation_id=uuid.UUID(cid), text="hi")
        ]


async def _versions(client: AsyncClient, h: dict[str, str], aid: str) -> dict[int, str]:
    items = (await client.get(f"{API}/assistants/{aid}/versions", headers=h)).json()["items"]
    return {v["version_number"]: v["status"] for v in items}


def _code(r: Response) -> str:
    return str(r.json()["error"]["code"])


@pytest.fixture
async def team(client: AsyncClient) -> Team:
    return await _team(client)


# ── nothing answers in Chat until an admin approves ──────────


async def test_a_submitted_version_answers_nobody_until_an_admin_approves_it(
    client: AsyncClient, team: Team
) -> None:
    builder, admin = team.h(team.member), team.h(team.admin)
    aid = await _assistant(client, builder)

    # A draft: no Chat conversation, from anyone.
    for h in (builder, admin):
        r = await _start(client, h, aid)
        assert (r.status_code, _code(r)) == (409, "assistant_unavailable")

    n = await submit(client, aid, builder, "first")
    assert await _versions(client, builder, aid) == {1: "submitted"}
    r = await _start(client, team.h(team.member2), aid)
    # Submitting is not approving: to anyone but its builders and admins it
    # isn't there at all (Phase 8.5: not live, not in Chat).
    assert (r.status_code, _code(r)) == (404, "not_found"), "submitting is not approving"

    # The builder cannot approve their own work, even as its creator.
    r = await _review(client, builder, aid, n, "approve")
    assert (r.status_code, _code(r)) == (403, "insufficient_role")

    r = await _review(client, admin, aid, n, "approve", note="Looks right")
    assert r.status_code == 200, r.text
    approved = r.json()
    assert approved["status"] == "approved"
    assert approved["reviewed_by"] == team.admin.user_id
    assert approved["review_note"] == "Looks right"
    detail = (await client.get(f"{API}/assistants/{aid}", headers=builder)).json()
    assert detail["status"] == "published"
    assert detail["current_version_id"] == approved["id"]
    assert (await _start(client, team.h(team.member2), aid)).status_code == 201

    async with get_sessionmaker()() as s:
        actions = (
            await s.scalars(select(AuditLog.action).where(AuditLog.target_id == approved["id"]))
        ).all()
    assert set(actions) == {"assistant.version.submit", "assistant.version.approve"}


async def test_a_new_submission_supersedes_the_one_waiting(client: AsyncClient, team: Team) -> None:
    builder, admin = team.h(team.member), team.h(team.admin)
    aid = await _assistant(client, builder)
    await submit(client, aid, builder)
    await submit(client, aid, builder)
    assert await _versions(client, builder, aid) == {1: "superseded", 2: "submitted"}

    r = await _review(client, admin, aid, 1, "approve")
    assert (r.status_code, _code(r)) == (409, "version_not_submitted")
    assert (await _review(client, admin, aid, 2, "approve")).status_code == 200
    r = await _review(client, admin, aid, 2, "approve")
    assert (r.status_code, _code(r)) == (409, "version_not_submitted"), "decided once"


async def test_a_rejection_says_why_and_a_withdrawal_is_the_builders(
    client: AsyncClient, team: Team
) -> None:
    builder, admin = team.h(team.member), team.h(team.admin)
    aid = await _assistant(client, builder)
    n = await submit(client, aid, builder)

    assert (await _review(client, admin, aid, n, "reject")).status_code == 422, "a reason"
    assert (await _review(client, builder, aid, n, "reject", note="no")).status_code == 403
    r = await _review(client, admin, aid, n, "reject", note="The refund policy is out of date")
    assert r.status_code == 200, r.text
    assert (r.json()["status"], r.json()["review_note"]) == (
        "rejected",
        "The refund policy is out of date",
    )
    assert (await _review(client, admin, aid, n, "approve")).status_code == 409
    assert (await _start(client, builder, aid)).status_code == 409

    n = await submit(client, aid, builder)
    stranger = team.h(team.member2)
    assert (await _review(client, stranger, aid, n, "withdraw")).status_code == 403
    r = await _review(client, builder, aid, n, "withdraw")
    assert (r.status_code, r.json()["status"]) == (200, "withdrawn")
    assert (await _review(client, admin, aid, n, "approve")).status_code == 409


async def test_a_builder_cannot_set_the_status_by_editing(client: AsyncClient, team: Team) -> None:
    """Before: PATCH took a status, so a builder could publish without
    approval, or reopen an assistant an admin had paused."""
    builder = team.h(team.member)
    aid = await _assistant(client, builder)
    for status in ("published", "archived", "draft"):
        r = await client.patch(f"{API}/assistants/{aid}", json={"status": status}, headers=builder)
        assert r.status_code == 422, r.text
    r = await client.patch(f"{API}/assistants/{aid}", json={"name": "Help desk"}, headers=builder)
    assert r.status_code == 200
    assert r.json()["status"] == "draft"
    async with get_sessionmaker()() as s:
        meta = await s.scalar(select(AuditLog.meta).where(AuditLog.action == "assistant.update"))
    assert meta == {"name": {"from": "Desk", "to": "Help desk"}}, "before and after"


# ── Chat never runs the draft ────────────────────────────────


async def test_chat_answers_from_the_approved_version_never_the_draft(
    client: AsyncClient, team: Team, spy: _PromptSpy
) -> None:
    builder, admin = team.h(team.member), team.h(team.admin)
    aid = await _assistant(client, builder, "Approved one.")
    n = await submit(client, aid, builder)
    await _review(client, admin, aid, n, "approve")
    await _prompt(client, builder, aid, "Unreviewed edit.")
    await submit(client, aid, builder)  # submitted, not approved

    cid = (await _start(client, team.h(team.member2), aid)).json()["id"]
    await _turn(cid)
    assert "Approved one." in spy.prompts[-1]
    assert "Unreviewed edit." not in spy.prompts[-1]


async def test_a_conversation_stays_on_the_version_it_started_on(
    client: AsyncClient, team: Team, spy: _PromptSpy
) -> None:
    """Before: every turn read whatever was live, so approving v2 changed a
    conversation mid-thread while its session still held v1's answers."""
    builder, admin = team.h(team.member), team.h(team.admin)
    person = team.h(team.member2)
    aid = await _assistant(client, builder, "You are version one.")
    await _review(client, admin, aid, await submit(client, aid, builder), "approve")
    old = (await _start(client, person, aid)).json()
    await _turn(old["id"])

    await _prompt(client, builder, aid, "You are version two.")
    await _review(client, admin, aid, await submit(client, aid, builder), "approve")
    await _turn(old["id"])
    new = (await _start(client, person, aid)).json()
    await _turn(new["id"])

    assert ["one" in p for p in spy.prompts] == [True, True, False]
    assert "version two" in spy.prompts[2]
    async with get_sessionmaker()() as s:
        runs = {
            str(r.conversation_id): r.version_number
            for r in await s.scalars(select(Run).order_by(Run.created_at))
        }
    assert runs == {old["id"]: 1, new["id"]: 2}


async def test_a_rollback_moves_the_conversations_of_the_version_rolled_back(
    client: AsyncClient, team: Team, spy: _PromptSpy
) -> None:
    builder, admin = team.h(team.member), team.h(team.admin)
    person = team.h(team.member2)
    aid = await _assistant(client, builder, "Good version.")
    await _review(client, admin, aid, await submit(client, aid, builder), "approve")
    on_v1 = (await _start(client, person, aid)).json()["id"]
    await _prompt(client, builder, aid, "Bad version.")
    await _review(client, admin, aid, await submit(client, aid, builder), "approve")
    on_v2 = (await _start(client, person, aid)).json()["id"]
    await _turn(on_v2)
    assert "Bad version." in spy.prompts[-1]

    r = await client.post(f"{API}/assistants/{aid}/versions/1/make-live", headers=builder)
    assert r.status_code == 403, "an admin's call"
    r = await client.post(f"{API}/assistants/{aid}/versions/1/make-live", headers=admin)
    assert r.status_code == 200, r.text
    assert await _versions(client, admin, aid) == {1: "approved", 2: "rolled_back"}

    await _turn(on_v2)
    assert "Good version." in spy.prompts[-1], "out of service: moved to the live version"
    await _turn(on_v1)
    assert "Good version." in spy.prompts[-1]
    fresh = (await _start(client, person, aid)).json()["id"]
    await _turn(fresh)
    assert "Good version." in spy.prompts[-1]

    r = await client.post(f"{API}/assistants/{aid}/versions/2/make-live", headers=admin)
    assert (r.status_code, _code(r)) == (409, "version_not_approved"), "resubmit it"
    r = await client.post(f"{API}/assistants/{aid}/versions/1/make-live", headers=admin)
    assert (r.status_code, _code(r)) == (409, "version_already_live")


async def test_approving_checks_what_the_version_names_still_holds(
    client: AsyncClient, team: Team
) -> None:
    """A connection deleted after submission would fail mid-conversation."""
    from tests.test_graph_references import _connection, _graph, _save

    builder, admin = team.h(team.member), team.h(team.admin)
    aid = await _assistant(client, builder)
    conn = await _connection(client, builder, aid, write=False)
    await _save(client, builder, aid, _graph(connection_id=conn))
    n = await submit(client, aid, builder)
    r = await client.delete(f"{API}/assistants/{aid}/db-connections/{conn}", headers=builder)
    assert r.status_code == 200, r.text

    r = await _review(client, admin, aid, n, "approve")
    assert (r.status_code, _code(r)) == (409, "version_invalid")
    assert await _versions(client, admin, aid) == {1: "submitted"}, "nothing recorded"


# ── previews ─────────────────────────────────────────────────


async def test_a_preview_is_for_builders_and_admins_and_runs_the_draft(
    client: AsyncClient, team: Team, spy: _PromptSpy
) -> None:
    builder, admin = team.h(team.member), team.h(team.admin)
    stranger = team.h(team.member2)
    aid = await _assistant(client, builder, "Draft in progress.")

    r = await _start(client, stranger, aid, PREVIEW)
    # Not its builder, and nothing of it is live: not found (Phase 8.5).
    assert (r.status_code, _code(r)) == (404, "not_found")
    preview = (await _start(client, builder, aid, PREVIEW)).json()
    assert preview["mode"] == "preview"
    await _turn(preview["id"])
    assert "Draft in progress." in spy.prompts[-1]

    # It follows the draft: that is what the builder is testing.
    await _prompt(client, builder, aid, "Draft, edited.")
    await _turn(preview["id"])
    assert "Draft, edited." in spy.prompts[-1]

    # Listed only in the Studio, and only to those who may preview.
    live = await client.get(f"{API}/assistants/{aid}/conversations", headers=builder)
    assert live.json()["items"] == []
    url = f"{API}/assistants/{aid}/conversations?mode=preview"
    assert [c["id"] for c in (await client.get(url, headers=builder)).json()["items"]] == [
        preview["id"]
    ]
    # Not found to a stranger, like the assistant itself (Phase 8.5).
    assert (await client.get(url, headers=stranger)).status_code == 404
    assert (await _start(client, admin, aid, PREVIEW)).status_code == 201


async def test_the_reviewing_admin_can_chat_with_the_submitted_version(
    client: AsyncClient, team: Team, spy: _PromptSpy
) -> None:
    builder, admin = team.h(team.member), team.h(team.admin)
    aid = await _assistant(client, builder, "What was submitted.")
    n = await submit(client, aid, builder)
    await _prompt(client, builder, aid, "Edited after submitting.")

    r = await _start(client, admin, aid, {**PREVIEW, "version": n})
    assert r.status_code == 201, r.text
    await _turn(r.json()["id"])
    assert "What was submitted." in spy.prompts[-1]

    r = await _start(client, admin, aid, {**PREVIEW, "version": 9})
    assert r.status_code == 404
    r = await _start(client, admin, aid, {"version": n})
    assert (r.status_code, _code(r)) == (400, "version_needs_preview")


async def test_a_preview_stops_answering_someone_who_may_no_longer_preview(
    client: AsyncClient, team: Team
) -> None:
    """A draft chat from before the gate became a preview; if its starter
    cannot edit the assistant, it does not go on running the draft."""
    builder = team.h(team.member)
    stranger = team.h(team.member2)
    aid = await _assistant(client, builder)
    r = await _start(client, builder, aid, PREVIEW)
    cid = r.json()["id"]
    async with get_sessionmaker()() as s:
        conv = await s.get(Conversation, uuid.UUID(cid))
        assert conv is not None
        conv.created_by = uuid.UUID(team.member2.user_id)
        await s.commit()
    r = await client.post(
        f"{API}/conversations/{cid}/messages", json={"text": "hi"}, headers=stranger
    )
    assert (r.status_code, _code(r)) == (403, "not_assistant_editor")


# ── pausing ──────────────────────────────────────────────────


async def test_pausing_stops_everything_and_reopening_brings_it_back(
    client: AsyncClient, team: Team, spy: _PromptSpy, monkeypatch: pytest.MonkeyPatch
) -> None:
    builder, admin = team.h(team.member), team.h(team.admin)
    person = team.h(team.member2)
    aid = await _assistant(client, builder, "Live answer.")
    await _review(client, admin, aid, await submit(client, aid, builder), "approve")
    cid = (await _start(client, person, aid)).json()["id"]
    preview = (await _start(client, builder, aid, PREVIEW)).json()["id"]
    async with get_sessionmaker()() as s:
        waiting = await waiting_request(
            s,
            conversation_id=uuid.UUID(cid),
            tool_name="mcp__caps__sql_query",
            tool_input={"sql": "UPDATE t SET x = 1"},
            risk="high",
            rationale="writes",
        )

    stopped: list[str] = []

    async def running(ids: list[uuid.UUID]) -> set[uuid.UUID]:
        return {i for i in ids if str(i) == cid}

    async def publish(conversation_id: Any) -> None:
        stopped.append(str(conversation_id))

    monkeypatch.setattr(assistants_svc.turns, "running_among", running)
    monkeypatch.setattr(assistants_svc.interrupts, "publish", publish)

    url = f"{API}/assistants/{aid}"
    assert (
        await client.post(f"{url}/archive", json={"reason": "x"}, headers=builder)
    ).status_code == 403
    assert (await client.post(f"{url}/archive", json={}, headers=admin)).status_code == 422
    r = await client.post(f"{url}/archive", json={"reason": "Wrong refund amounts"}, headers=admin)
    assert (r.status_code, r.json()["status"]) == (200, "archived")
    assert stopped == [cid], "the answer being written is stopped"

    async with get_sessionmaker()() as s:
        row = await s.get(Approval, waiting.id)
        assert row is not None and row.status is ApprovalStatus.expired

    for who, conv in ((person, cid), (builder, preview)):
        r = await client.post(
            f"{API}/conversations/{conv}/messages", json={"text": "hi"}, headers=who
        )
        assert (r.status_code, _code(r)) == (409, "assistant_unavailable")
    # Paused, it is gone from Chat for people (Phase 8.5); its builders are
    # told why.
    assert (await _start(client, person, aid)).status_code == 404
    assert (await _start(client, builder, aid, PREVIEW)).status_code == 409
    # A pause that lands between the check and the turn still stops it.
    events = await _turn(cid)
    assert [e.code for e in events if e.type == "error"] == ["assistant_unavailable"]

    # Approving while paused does not reopen it.
    await _prompt(client, builder, aid, "Fixed answer.")
    await _review(client, admin, aid, await submit(client, aid, builder), "approve")
    assert (await client.get(url, headers=admin)).json()["status"] == "archived"

    assert (await client.post(f"{url}/unarchive", headers=builder)).status_code == 403
    r = await client.post(f"{url}/unarchive", headers=admin)
    assert (r.status_code, r.json()["status"]) == (200, "published")
    fresh = (await _start(client, person, aid)).json()["id"]
    await _turn(fresh)
    assert "Fixed answer." in spy.prompts[-1]

    async with get_sessionmaker()() as s:
        logged = await s.scalar(select(AuditLog.meta).where(AuditLog.action == "assistant.archive"))
    assert logged == {"from": "published", "reason": "Wrong refund amounts", "approvals_expired": 1}


async def test_reopening_an_assistant_never_approved_leaves_it_a_draft(
    client: AsyncClient, team: Team
) -> None:
    admin = team.h(team.admin)
    aid = await _assistant(client, team.h(team.member))
    await client.post(f"{API}/assistants/{aid}/archive", json={"reason": "x"}, headers=admin)
    r = await client.post(f"{API}/assistants/{aid}/unarchive", headers=admin)
    assert r.json()["status"] == "draft"
    assert (await _start(client, admin, aid)).status_code == 409


async def test_a_conversation_from_before_the_gate_with_no_version_goes_to_the_live_one(
    client: AsyncClient, team: Team, spy: _PromptSpy
) -> None:
    builder, admin = team.h(team.member), team.h(team.admin)
    aid = await _assistant(client, builder, "Live.")
    await _review(client, admin, aid, await submit(client, aid, builder), "approve")
    cid = (await _start(client, team.h(team.member2), aid)).json()["id"]
    async with get_sessionmaker()() as s:
        conv = await s.get(Conversation, uuid.UUID(cid))
        assert conv is not None and conv.mode is ConversationMode.live
        conv.assistant_version_id = None
        await s.commit()
    await _turn(cid)
    assert "Live." in spy.prompts[-1]


async def test_an_http_tool_with_no_allowed_sites_cannot_be_approved(
    client: AsyncClient, team: Team
) -> None:
    """A builder may leave it in a draft (7a.9 warns); an admin may not put
    a tool that refuses every request in front of everyone."""
    builder, admin = team.h(team.member), team.h(team.admin)
    aid = await _assistant(client, builder)
    cfg = (await client.get(f"{API}/assistants/{aid}", headers=builder)).json()["draft_config"]
    cfg["tools"]["http_request"] = {"enabled": True, "allowed_domains": []}
    assert (
        await client.put(f"{API}/assistants/{aid}/draft-config", json=cfg, headers=builder)
    ).status_code == 200
    n = await submit(client, aid, builder)
    r = await _review(client, admin, aid, n, "approve")
    assert (r.status_code, _code(r)) == (409, "version_not_approvable")
    assert "no allowed sites" in r.json()["error"]["message"]

    cfg["tools"]["http_request"]["allowed_domains"] = ["example.com"]
    await client.put(f"{API}/assistants/{aid}/draft-config", json=cfg, headers=builder)
    assert (
        await _review(client, admin, aid, await submit(client, aid, builder), "approve")
    ).status_code == 200


async def test_a_decision_only_moves_a_version_still_waiting(
    client: AsyncClient, team: Team
) -> None:
    """Rejecting or withdrawing is for a submission: an approved, live
    version can't be "withdrawn" by its builder or rejected after the fact.
    Taking it out of service is a rollback or a pause."""
    builder, admin = team.h(team.member), team.h(team.admin)
    aid = await _assistant(client, builder)
    n = await submit(client, aid, builder)
    await _review(client, admin, aid, n, "approve")

    r = await _review(client, builder, aid, n, "withdraw")
    assert (r.status_code, _code(r)) == (409, "version_not_submitted")
    r = await _review(client, admin, aid, n, "reject", note="Too late")
    assert (r.status_code, _code(r)) == (409, "version_not_submitted")
    assert await _versions(client, admin, aid) == {1: "approved"}
    assert (await _start(client, team.h(team.member2), aid)).status_code == 201
