"""Hand-offs to a person (Phase 9.5).

- Only where the assistant's hand-off is on, never from a preview, and once
  in a while per conversation.
- The email links to the conversation in the app and carries none of it;
  the summary (built without a model call, secrets and links stripped from
  the note) stays in the app.
- With a ticket action, the declared HTTP call is made as the person with
  the title and summary, and an approver-tier action can't be chosen.
- The assistant's own `hand_off` tool does the same, without asking.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from app.db.session import get_sessionmaker
from app.mail import backends
from app.models.audit_log import AuditLog
from app.models.conversation import Message, MessageRole
from app.models.handoff import Handoff
from app.security import ssrf
from app.services import handoffs
from httpx import AsyncClient
from sqlalchemy import select, update
from tests.versions import PREVIEW, go_live

from test_http_actions import (  # type: ignore[import-not-found]
    BASE,
    SECRET,
    Sent,
    _chat,
    _h,
    _org,
    _turn,
)

pytestmark = pytest.mark.anyio

API = "/api/v1"
# An admin of the organisation (Phase 9.9): only an admin may open the link.
CONTACT = "owner@example.com"
KEY = "sk-ant-" + "x" * 30  # shaped like a key, not one
QUESTION = "My laptop won't join the office Wi-Fi since this morning"
ANSWER = "Try forgetting the network and joining again."
TICKET = {
    "name": "open_ticket",
    "title": "Open an IT ticket",
    "method": "POST",
    "path": "/tickets",
    "params": [
        {"name": "title"},
        {"name": "summary", "max_length": 4000},
        {"name": "note", "required": False},
    ],
    "tier": "confirm",
}


@pytest.fixture(autouse=True)
def _empty_outbox() -> Any:
    backends.outbox.clear()
    yield
    backends.outbox.clear()


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> Sent:
    recorder = Sent(status=201, body={"id": "T-42", "status": "open"})
    monkeypatch.setattr(ssrf, "safe_request", recorder)
    return recorder


async def _assistant(
    client: AsyncClient,
    h: dict[str, str],
    *,
    handoff: dict[str, Any] | None = None,
    action: dict[str, Any] | None = None,
    name: str = "IT desk",
) -> str:
    a = (await client.post(f"{API}/assistants", json={"name": name}, headers=h)).json()
    cfg = a["draft_config"]
    if action is not None:
        conn = await client.post(
            f"{API}/assistants/{a['id']}/http-connections",
            json={
                "name": "Tickets",
                "base_url": BASE,
                "headers": {"Authorization": SECRET},
                "send_asker": True,
            },
            headers=h,
        )
        assert conn.status_code == 201, conn.text
        cfg["tools"]["http_request"] = {
            "enabled": True,
            "allowed_domains": [],
            "actions": [{**action, "connection_id": str(conn.json()["id"])}],
        }
    if handoff is not None:
        cfg["handoff"] = handoff
    r = await client.put(f"{API}/assistants/{a['id']}/draft-config", json=cfg, headers=h)
    assert r.status_code == 200, r.text
    return str(a["id"])


async def _said(cid: str, org_id: str) -> None:
    """A question and an answer, as a turn would have left them."""
    async with get_sessionmaker()() as s:
        for role, text in ((MessageRole.user, QUESTION), (MessageRole.assistant, ANSWER)):
            s.add(
                Message(
                    conversation_id=uuid.UUID(cid),
                    org_id=uuid.UUID(org_id),
                    role=role,
                    content=text,
                    blocks=[],
                )
            )
        await s.commit()


async def _rows(cid: str) -> list[Handoff]:
    async with get_sessionmaker()() as s:
        return list(
            (
                await s.scalars(select(Handoff).where(Handoff.conversation_id == uuid.UUID(cid)))
            ).all()
        )


async def _audits() -> list[AuditLog]:
    async with get_sessionmaker()() as s:
        return list(
            (
                await s.scalars(select(AuditLog).where(AuditLog.action == "conversation.handoff"))
            ).all()
        )


def _mails() -> list[Any]:
    return [m for m in backends.outbox if m.kind == "handoff"]


# ── where it is offered ──────────────────────────────────────


async def test_an_assistant_without_hand_off_refuses(client: AsyncClient) -> None:
    org_id, people = await _org(client)
    h = _h(org_id, people["owner"])
    aid = await _assistant(client, h)
    await go_live(client, aid, h)
    ph = _h(org_id, people["pat"])
    cid = await _chat(client, ph, aid, preview=False)

    r = await client.post(f"{API}/conversations/{cid}/handoff", json={}, headers=ph)
    assert (r.status_code, r.json()["error"]["code"]) == (400, "handoff_off")
    listed = (await client.get(f"{API}/conversations/{cid}/handoffs", headers=ph)).json()
    assert (listed["available"], listed["contact_email"], listed["items"]) == (False, "", [])
    assert _mails() == [] and await _audits() == []


async def test_turning_it_on_needs_an_address(client: AsyncClient) -> None:
    org_id, people = await _org(client)
    h = _h(org_id, people["owner"])
    a = (await client.post(f"{API}/assistants", json={"name": "IT desk"}, headers=h)).json()
    cfg = {**a["draft_config"], "handoff": {"enabled": True, "contact_email": ""}}
    r = await client.put(f"{API}/assistants/{a['id']}/draft-config", json=cfg, headers=h)
    assert r.status_code == 422
    cfg["handoff"]["contact_email"] = "not an address"
    r = await client.put(f"{API}/assistants/{a['id']}/draft-config", json=cfg, headers=h)
    assert r.status_code == 422


async def test_a_preview_is_never_handed_off(client: AsyncClient) -> None:
    org_id, people = await _org(client)
    h = _h(org_id, people["owner"])
    aid = await _assistant(client, h, handoff={"enabled": True, "contact_email": CONTACT})
    preview = await client.post(f"{API}/assistants/{aid}/conversations", json=PREVIEW, headers=h)
    assert preview.status_code == 201, preview.text
    cid = preview.json()["id"]

    r = await client.post(f"{API}/conversations/{cid}/handoff", json={}, headers=h)
    assert (r.status_code, r.json()["error"]["code"]) == (400, "preview_no_handoff")
    listed = (await client.get(f"{API}/conversations/{cid}/handoffs", headers=h)).json()
    assert listed["available"] is False
    assert _mails() == []


# ── by email ─────────────────────────────────────────────────


async def test_the_email_links_to_the_conversation_and_carries_none_of_it(
    client: AsyncClient,
) -> None:
    org_id, people = await _org(client)
    h = _h(org_id, people["owner"])
    aid = await _assistant(client, h, handoff={"enabled": True, "contact_email": CONTACT})
    await go_live(client, aid, h)
    ph = _h(org_id, people["pat"])
    cid = await _chat(client, ph, aid, preview=False)
    await _said(cid, org_id)

    listed = (await client.get(f"{API}/conversations/{cid}/handoffs", headers=ph)).json()
    assert (listed["available"], listed["contact_email"]) == (True, CONTACT)
    assert listed["opens_ticket"] is False

    other = await client.post(f"{API}/conversations/{cid}/handoff", json={}, headers=h)
    assert other.status_code == 403, "only the person who started it"

    note = f"Still broken, see https://evil.example.com/x and my key {KEY}"
    r = await client.post(f"{API}/conversations/{cid}/handoff", json={"note": note}, headers=ph)
    assert r.status_code == 201, r.text
    body = r.json()
    assert (body["status"], body["channel"], body["ticket_ref"]) == ("sent", "email", None)

    summary = body["summary"]
    assert QUESTION in summary and ANSWER in summary and "Still broken" in summary
    assert "evil.example.com" not in summary, "links come out of the note"
    assert KEY not in summary, "and secrets"

    (mail,) = _mails()
    assert mail.to == CONTACT
    assert mail.subject == "pat asked for help with IT desk"
    assert f"/assistants/{aid}/chat?c={cid}" in mail.text
    for content in (QUESTION, ANSWER, "Still broken", summary):
        assert content not in mail.text, "no conversation content in an email"

    (row,) = await _audits()
    assert row.actor_user_id == uuid.UUID(people["pat"].user_id)
    assert row.target_id == cid
    assert row.meta["channel"] == "email" and row.meta["handoff_id"] == body["id"]
    assert row.meta["by"] == "person"

    (shown,) = (await client.get(f"{API}/conversations/{cid}/handoffs", headers=ph)).json()["items"]
    assert shown["id"] == body["id"] and shown["summary"] == summary


async def test_one_hand_off_per_conversation_in_ten_minutes(client: AsyncClient) -> None:
    org_id, people = await _org(client)
    h = _h(org_id, people["owner"])
    aid = await _assistant(client, h, handoff={"enabled": True, "contact_email": CONTACT})
    await go_live(client, aid, h)
    ph = _h(org_id, people["pat"])
    cid = await _chat(client, ph, aid, preview=False)

    first = await client.post(f"{API}/conversations/{cid}/handoff", json={}, headers=ph)
    assert first.status_code == 201, first.text
    again = await client.post(f"{API}/conversations/{cid}/handoff", json={}, headers=ph)
    assert (again.status_code, again.json()["error"]["code"]) == (409, "handoff_recent")
    assert len(_mails()) == 1

    async with get_sessionmaker()() as s:
        await s.execute(
            update(Handoff)
            .where(Handoff.conversation_id == uuid.UUID(cid))
            .values(created_at=datetime.now(UTC) - handoffs.WINDOW - timedelta(minutes=1))
        )
        await s.commit()
    later = await client.post(f"{API}/conversations/{cid}/handoff", json={}, headers=ph)
    assert later.status_code == 201, later.text
    assert len(await _rows(cid)) == 2 and len(_mails()) == 2


# ── by ticket ────────────────────────────────────────────────


async def test_a_ticket_action_is_called_as_the_person_with_the_summary(
    client: AsyncClient, sent: Sent
) -> None:
    org_id, people = await _org(client)
    h = _h(org_id, people["owner"])
    aid = await _assistant(
        client,
        h,
        handoff={"enabled": True, "contact_email": CONTACT, "action": "open_ticket"},
        action=TICKET,
    )
    await go_live(client, aid, h)
    ph = _h(org_id, people["pat"])
    cid = await _chat(client, ph, aid, preview=False)
    await _said(cid, org_id)
    listed = (await client.get(f"{API}/conversations/{cid}/handoffs", headers=ph)).json()
    assert listed["opens_ticket"] is True

    r = await client.post(
        f"{API}/conversations/{cid}/handoff", json={"note": "Urgent, please"}, headers=ph
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert (body["status"], body["channel"]) == ("sent", "action")
    assert "T-42" in (body["ticket_ref"] or "")

    (call,) = sent.calls
    assert (call["method"], call["url"]) == ("POST", f"{BASE}/tickets")
    assert call["headers"]["Authorization"] == SECRET
    assert call["headers"]["X-Asker-Email"] == "pat@example.com"
    sent_body = json.loads(call["body"])
    assert QUESTION in sent_body["summary"] and sent_body["note"] == "Urgent, please"
    assert "IT desk" in sent_body["title"]
    assert _mails() == [], "the ticket instead of the email"
    (row,) = await _audits()
    assert row.meta["channel"] == "action" and row.meta["action"] == "open_ticket"


async def test_a_failed_ticket_is_recorded_and_may_be_tried_again(
    client: AsyncClient, sent: Sent
) -> None:
    org_id, people = await _org(client)
    h = _h(org_id, people["owner"])
    aid = await _assistant(
        client,
        h,
        handoff={"enabled": True, "contact_email": CONTACT, "action": "open_ticket"},
        action=TICKET,
    )
    await go_live(client, aid, h)
    ph = _h(org_id, people["pat"])
    cid = await _chat(client, ph, aid, preview=False)

    sent.status = 503
    r = await client.post(f"{API}/conversations/{cid}/handoff", json={}, headers=ph)
    assert r.status_code == 201, r.text
    assert (r.json()["status"], r.json()["channel"]) == ("failed", "action")
    sent.status = 201
    again = await client.post(f"{API}/conversations/{cid}/handoff", json={}, headers=ph)
    assert again.status_code == 201 and again.json()["status"] == "sent", "a failure doesn't count"


async def test_an_action_that_waits_for_an_approver_cant_be_chosen(client: AsyncClient) -> None:
    org_id, people = await _org(client)
    h = _h(org_id, people["owner"])
    aid = await _assistant(
        client,
        h,
        handoff={"enabled": True, "contact_email": CONTACT, "action": "open_ticket"},
        action={**TICKET, "tier": "approver"},
    )
    r = await client.post(f"{API}/assistants/{aid}/versions", json={"note": ""}, headers=h)
    assert (r.status_code, r.json()["error"]["code"]) == (400, "graph_invalid")
    codes = [e["code"] for e in r.json()["error"]["details"]["errors"]]
    assert "handoff_approver_action" in codes


async def test_an_email_contact_must_be_able_to_open_the_conversation(
    client: AsyncClient,
) -> None:
    """Phase 9.9: the email links to the conversation, which only an admin
    may open, so the contact has to be one."""
    org_id, people = await _org(client)
    h = _h(org_id, people["owner"])
    aid = await _assistant(
        client, h, handoff={"enabled": True, "contact_email": "someone-else@example.com"}
    )
    r = await client.post(f"{API}/assistants/{aid}/versions", json={"note": ""}, headers=h)
    assert (r.status_code, r.json()["error"]["code"]) == (400, "graph_invalid")
    codes = [e["code"] for e in r.json()["error"]["details"]["errors"]]
    assert "handoff_contact_not_admin" in codes


# ── the assistant's own tool ─────────────────────────────────


async def test_the_assistant_hands_off_without_asking(client: AsyncClient) -> None:
    org_id, people = await _org(client)
    h = _h(org_id, people["owner"])
    aid = await _assistant(client, h, handoff={"enabled": True, "contact_email": CONTACT})
    await go_live(client, aid, h)
    ph = _h(org_id, people["pat"])
    cid = await _chat(client, ph, aid, preview=False)

    events = await _turn(cid, 'mcp: caps.hand_off {"note": "Needs a Wi-Fi reset by IT"}')
    assert not [e for e in events if e.type == "approval_required"]
    (result,) = [e for e in events if e.type == "tool_result"]
    assert result.status == "success", result.output
    assert CONTACT in result.output
    (row,) = await _rows(cid)
    assert "Needs a Wi-Fi reset by IT" in row.summary
    (mail,) = _mails()
    assert mail.to == CONTACT and "Wi-Fi" not in mail.text
    (audit_row,) = await _audits()
    assert audit_row.meta["by"] == "assistant"

    events = await _turn(cid, 'mcp: caps.hand_off {"note": "Again"}')
    (result,) = [e for e in events if e.type == "tool_result"]
    assert result.status == "error" and "handed to a person" in result.output


async def test_the_tool_is_offered_only_when_hand_off_is_on(client: AsyncClient) -> None:
    org_id, people = await _org(client)
    h = _h(org_id, people["owner"])
    off = await _assistant(client, h, name="Without")
    events = await _turn(
        await _chat(client, h, off, preview=True), 'mcp: caps.hand_off {"note": "x"}'
    )
    assert not [e for e in events if e.type == "tool_result"]

    on = await _assistant(client, h, handoff={"enabled": True, "contact_email": CONTACT})
    cid = await _chat(client, h, on, preview=True)
    events = await _turn(cid, 'mcp: caps.hand_off {"note": "x"}')
    (result,) = [e for e in events if e.type == "tool_result"]
    assert result.status == "success" and "preview, so nobody was told" in result.output
    assert await _rows(cid) == [] and _mails() == []
