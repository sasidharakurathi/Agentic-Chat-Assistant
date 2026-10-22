"""The Employee role (Phase 8.1, plan D2).

Employees chat with what answers in Chat and build nothing. The server
leaves the working parts of an answer out of everything an employee
receives: the input and output of a tool, the model, tokens and cost,
the model's thinking, budget warnings. Their requests for a tool that
needs approval wait for an admin, who hears of them; the employee can't
decide them. Invites make employees unless someone chooses otherwise.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from app.db.session import get_sessionmaker
from app.mail import backends
from app.models.approval import ApproverNotice
from app.services import chat_view
from httpx import AsyncClient
from sqlalchemy import select
from tests.pending import decide, waiting_request
from tests.versions import go_live

from test_orgs import Actor, _register  # type: ignore[import-not-found]

pytestmark = pytest.mark.anyio

API = "/api/v1"


@pytest.fixture(autouse=True)
def _empty_outbox() -> Any:
    backends.outbox.clear()
    yield
    backends.outbox.clear()


class Org:
    def __init__(self, org_id: str, people: dict[str, Actor]) -> None:
        self.id = org_id
        self.people = people

    def h(self, who: str) -> dict[str, str]:
        return {**self.people[who].headers, "X-Org-Id": self.id}


async def _org(client: AsyncClient) -> Org:
    """An owner, a builder (member) and an employee, invited the default way."""
    owner = await _register(client, "owner@example.com")
    org = (await client.post(f"{API}/orgs", json={"name": "Acme"}, headers=owner.headers)).json()
    people = {"owner": owner}
    for name, role in (("builder", "member"), ("employee", None)):
        actor = await _register(client, f"{name}@example.com")
        body = {"email": f"{name}@example.com", **({"role": role} if role else {})}
        inv = await client.post(f"{API}/orgs/{org['id']}/invites", json=body, headers=owner.headers)
        assert inv.status_code == 201, inv.text
        token = inv.json()["accept_url"].rsplit("/", 1)[-1]
        assert (
            await client.post(f"{API}/invites/{token}/accept", headers=actor.headers)
        ).is_success
        people[name] = actor
    return Org(org["id"], people)


@pytest.fixture
async def org(client: AsyncClient) -> Org:
    return await _org(client)


async def _assistant(client: AsyncClient, org: Org, *, live: bool, memory: bool = True) -> str:
    h = org.h("builder")
    a = (await client.post(f"{API}/assistants", json={"name": "Helpdesk"}, headers=h)).json()
    cfg = a["draft_config"]
    cfg["memory"] = {**cfg["memory"], "memory_tool": memory}
    assert (
        await client.put(f"{API}/assistants/{a['id']}/draft-config", json=cfg, headers=h)
    ).status_code == 200
    if live:
        await go_live(client, a["id"], h, approver=org.h("owner"))
    return str(a["id"])


async def _turn(client: AsyncClient, h: dict[str, str], cid: str, text: str) -> list[str]:
    """The `data:` payloads of one turn's stream, as sent."""
    frames: list[str] = []
    async with client.stream(
        "POST", f"{API}/conversations/{cid}/messages", json={"text": text}, headers=h
    ) as stream:
        assert stream.status_code == 200
        async for line in stream.aiter_lines():
            if line.startswith("data: "):
                frames.append(line[len("data: ") :])
    return frames


# ── who an employee is ───────────────────────────────────────


async def test_an_invite_makes_an_employee_unless_someone_chooses(
    client: AsyncClient, org: Org
) -> None:
    me = (await client.get(f"{API}/auth/me", headers=org.people["employee"].headers)).json()
    assert [m["role"] for m in me["memberships"] if m["org_id"] == org.id] == ["employee"]


async def test_the_studio_is_closed_to_an_employee(client: AsyncClient, org: Org) -> None:
    for method, url in (
        ("GET", "/assistants"),
        ("POST", "/assistants"),
        ("GET", f"/orgs/{org.id}/usage"),
    ):
        r = await client.request(method, f"{API}{url}", headers=org.h("employee"), json={})
        assert (r.status_code, r.json()["error"]["code"]) == (403, "studio_access_required"), url


# ── what an employee can reach ───────────────────────────────


async def test_the_catalog_lists_only_what_answers_in_chat(client: AsyncClient, org: Org) -> None:
    live = await _assistant(client, org, live=True)
    draft = await _assistant(client, org, live=False)
    listed = (await client.get(f"{API}/chat/assistants", headers=org.h("employee"))).json()
    assert [(a["id"], a["version_number"], a["can_edit"]) for a in listed] == [(live, 1, False)]
    assert (
        await client.get(f"{API}/chat/assistants/{draft}", headers=org.h("employee"))
    ).status_code == 404
    # The builder may open their own draft's card, for its preview.
    card = await client.get(f"{API}/chat/assistants/{draft}", headers=org.h("builder"))
    assert (card.status_code, card.json()["can_edit"]) == (200, True)
    # Not in the catalog, though, even for its builder or an admin.
    for who in ("builder", "owner"):
        listed = (await client.get(f"{API}/chat/assistants", headers=org.h(who))).json()
        assert [a["id"] for a in listed] == [live], who


async def test_an_employee_chats_live_and_never_previews(client: AsyncClient, org: Org) -> None:
    aid = await _assistant(client, org, live=True)
    url = f"{API}/assistants/{aid}/conversations"
    preview = await client.post(url, json={"mode": "preview"}, headers=org.h("employee"))
    assert (preview.status_code, preview.json()["error"]["code"]) == (403, "not_assistant_editor")
    live = await client.post(url, json={}, headers=org.h("employee"))
    assert live.status_code == 201, live.text
    mine = (await client.get(f"{API}/chat/conversations", headers=org.h("employee"))).json()
    assert [c["id"] for c in mine["items"]] == [live.json()["id"]]
    assert mine["items"][0]["assistant_name"] == "Helpdesk"
    # Not the builder's: conversations are private.
    theirs = (await client.get(f"{API}/chat/conversations", headers=org.h("builder"))).json()
    assert theirs["items"] == []


# ── what an employee receives ────────────────────────────────


async def test_an_employee_gets_the_answer_not_its_workings(client: AsyncClient, org: Org) -> None:
    aid = await _assistant(client, org, live=True)
    h = org.h("employee")
    cid = (await client.post(f"{API}/assistants/{aid}/conversations", json={}, headers=h)).json()[
        "id"
    ]
    frames = await _turn(client, h, cid, "remember: my laptop is a ThinkPad")
    events = [json.loads(f) for f in frames]
    kinds = [e["type"] for e in events]
    assert "tool_call" in kinds and "done" in kinds
    assert not {"usage", "thinking", "budget"} & set(kinds)
    # The memory tool may be called more than once (look, then write).
    calls = [e for e in events if e["type"] == "tool_call"]
    assert calls and all((c["name"], c["input"]) == ("Checked its notes", {}) for c in calls)
    assert all(e.get("output", "") == "" for e in events if e["type"] == "tool_result")

    detail = (await client.get(f"{API}/conversations/{cid}", headers=h)).json()
    assert (detail["cost_usd"], detail["token_usage"]) in (("0", {}), (0, {}), ("0.0", {}))
    answer = detail["messages"][-1]
    assert (answer["model"], answer["tokens_in"], answer["tokens_out"]) == (None, 0, 0)
    blocks = [b for b in answer["blocks"] if b["type"] == "tool_call"]
    assert blocks and all("input" not in b and "output" not in b for b in blocks)
    assert {b["name"] for b in blocks} == {"Checked its notes"}


async def test_a_builder_still_sees_the_workings(client: AsyncClient, org: Org) -> None:
    aid = await _assistant(client, org, live=True)
    h = org.h("builder")
    cid = (await client.post(f"{API}/assistants/{aid}/conversations", json={}, headers=h)).json()[
        "id"
    ]
    events = [json.loads(f) for f in await _turn(client, h, cid, "remember: tea, not coffee")]
    calls = [e for e in events if e["type"] == "tool_call"]
    assert calls and all(c["name"].endswith("memory") and c["input"] != {} for c in calls)
    assert "usage" in [e["type"] for e in events]


# ── requests that need approval ──────────────────────────────


async def _request(client: AsyncClient, org: Org, who: str) -> tuple[str, str]:
    aid = await _assistant(client, org, live=True)
    h = org.h(who)
    cid = (await client.post(f"{API}/assistants/{aid}/conversations", json={}, headers=h)).json()[
        "id"
    ]
    async with get_sessionmaker()() as s:
        row = await waiting_request(
            s,
            conversation_id=uuid.UUID(cid),
            tool_input={"sql": "UPDATE laptops SET owner = 'me'"},
            rationale="UPDATE laptops SET owner = 'me'",
        )
    return cid, str(row.id)


async def test_an_employees_request_waits_for_an_approver(client: AsyncClient, org: Org) -> None:
    cid, approval_id = await _request(client, org, "employee")

    mine = await client.get(f"{API}/conversations/{cid}/approvals", headers=org.h("employee"))
    (shown,) = mine.json()["items"]
    assert (shown["tool_name"], shown["input"], shown["rationale"]) == ("Looked up data", {}, None)
    assert "UPDATE" not in mine.text

    r = await decide(client, org.h("employee"), approval_id, "approved")
    assert (r.status_code, r.json()["error"]["code"]) == (403, "cannot_decide_own")

    # The approvers' queue (Phase 9.1) shows what would run, so it can be
    # decided there: the request, never the conversation.
    (waiting,) = (await client.get(f"{API}/approvals/decidable", headers=org.h("owner"))).json()
    assert (waiting["id"], waiting["asker_email"], waiting["rationale"]) == (
        approval_id,
        "employee@example.com",
        "UPDATE laptops SET owner = 'me'",
    )
    assert (await client.get(f"{API}/approvals/decidable", headers=org.h("builder"))).json() == []

    decided = await decide(client, org.h("owner"), approval_id, "denied", note="IT does this.")
    assert decided.status_code == 200, decided.text


async def test_the_approvers_hear_of_a_request_never_the_asker(
    client: AsyncClient, org: Org
) -> None:
    await _request(client, org, "employee")
    told = sorted(m.to for m in backends.outbox if m.kind == "approval_waiting")
    assert told == ["owner@example.com"]
    (sent,) = [m for m in backends.outbox if m.kind == "approval_waiting"]
    assert "UPDATE" not in sent.text and "/approvals" in sent.text

    # A builder's waits for an approver too (Phase 9.1), and they aren't told.
    # The owner was just emailed, so this one waits for their next email
    # (Phase 9.2: a burst is one email).
    backends.outbox.clear()
    await _request(client, org, "builder")
    assert [m for m in backends.outbox if m.kind == "approval_waiting"] == []
    async with get_sessionmaker()() as s:
        (held,) = (await s.scalars(select(ApproverNotice))).all()
    assert held.user_id == uuid.UUID(org.people["owner"].user_id) and held.pending_since


# ── an employee builds nothing ───────────────────────────────


async def test_a_builder_demoted_to_employee_edits_nothing_they_built(
    client: AsyncClient, org: Org
) -> None:
    aid = await _assistant(client, org, live=True)
    builder_id = org.people["builder"].user_id
    r = await client.patch(
        f"{API}/orgs/{org.id}/members/{builder_id}",
        json={"role": "employee"},
        headers=org.h("owner"),
    )
    assert r.status_code == 200, r.text
    preview = await client.post(
        f"{API}/assistants/{aid}/conversations", json={"mode": "preview"}, headers=org.h("builder")
    )
    assert preview.status_code == 403
    card = (await client.get(f"{API}/chat/assistants/{aid}", headers=org.h("builder"))).json()
    assert card["can_edit"] is False
    assert (await submit_refused(client, aid, org.h("builder"))) == "studio_access_required"


async def submit_refused(client: AsyncClient, aid: str, h: dict[str, str]) -> str:
    r = await client.post(f"{API}/assistants/{aid}/versions", json={"note": ""}, headers=h)
    assert r.status_code == 403
    return str(r.json()["error"]["code"])


async def test_an_employee_is_no_co_editor_and_no_heir(client: AsyncClient, org: Org) -> None:
    aid = await _assistant(client, org, live=False)
    employee_id = org.people["employee"].user_id
    r = await client.post(
        f"{API}/assistants/{aid}/editors", json={"user_id": employee_id}, headers=org.h("owner")
    )
    assert (r.status_code, r.json()["error"]["code"]) == (400, "not_a_builder")
    r = await client.post(
        f"{API}/orgs/{org.id}/members/{org.people['builder'].user_id}:deactivate",
        json={"transfer_to": employee_id},
        headers=org.h("owner"),
    )
    assert (r.status_code, r.json()["error"]["code"]) == (400, "invalid_transfer")


# ── the shaping itself ───────────────────────────────────────


def test_frames_leave_the_workings_out() -> None:
    assert chat_view.frame('data: {"type":"usage","cost_usd":0.12}\n\n') is None
    assert chat_view.frame('data: {"type":"thinking","text":"the rows say"}\n\n') is None
    token = 'data: {"type":"token","text":"Hi"}\n\n'
    assert json.loads(chat_view.frame(token)[6:]) == {"type": "token", "text": "Hi"}  # type: ignore[index]
    shaped = chat_view.frame(
        'data: {"type":"approval_required","approval_id":"a","tool":"mcp__caps__sql_query",'
        '"input":{"sql":"DELETE FROM t"},"rationale":"DELETE FROM t","risk":"high"}\n\n'
    )
    assert shaped is not None and "DELETE" not in shaped and '"decided_by":"approver"' in shaped
    # A declared action's values are the asker's own, to check on their card
    # (Phase 9.1); its statement still never reaches them.
    mine = chat_view.frame(
        'data: {"type":"approval_required","approval_id":"b","tool":"mcp__caps__act_request",'
        '"input":{"app":"Figma"},"rationale":"INSERT INTO access_requests","risk":"medium",'
        '"tier":"asker","action_name":"request"}\n\n'
    )
    assert mine is not None
    data = json.loads(mine[6:])
    assert (data["input"], data["rationale"], data["decided_by"]) == ({"app": "Figma"}, "", "asker")
    assert chat_view.frame("not json") == "not json"


def test_tool_labels_are_words() -> None:
    assert chat_view.tool_label("mcp__caps__kb_search") == "Searched the knowledge base"
    assert chat_view.tool_label("mcp__jira__create_issue") == "Used jira"
    assert chat_view.tool_label("something_new") == "Used a tool"
    assert chat_view.tool_label("mcp__caps__act_request_access") == "Request access"
