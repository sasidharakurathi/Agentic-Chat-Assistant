"""HTTP connections and actions (Phase 9.3).

A connection is a company system's address and the headers it needs,
sealed. An action is one declared call on it: the builder writes the
method and the path with {slots}, the model only fills the values, and the
platform adds who is asking when the connection says so. The tiers are SQL
actions' (9.1). `safe_request` is replaced by a recorder, so every test
sees exactly what would have been sent, and nothing leaves the machine.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from app.agent import http_actions
from app.agent.actions import ActionInputError, Asker
from app.db.session import get_sessionmaker
from app.models.approval import Approval, ApprovalStatus
from app.models.audit_log import AuditLog
from app.models.integration import HttpConnection
from app.schemas.assistant_config import AssistantConfig, HttpAction
from app.security import ssrf
from app.security.ssrf import FetchResult
from app.services import chat as chat_svc
from app.services import executor, manifest
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import select
from tests.pending import decide
from tests.versions import go_live

from test_orgs import Actor, _register  # type: ignore[import-not-found]

pytestmark = pytest.mark.anyio

API = "/api/v1"
SECRET = "Bearer s3cret-token-do-not-show"
BASE = "https://tickets.corp.example.com/api"

TICKET_STATUS = {
    "name": "ticket_status",
    "title": "Look up a ticket",
    "method": "GET",
    "path": "/tickets/{ticket_id}",
    "params": [{"name": "ticket_id", "type": "integer", "description": "The ticket"}],
    "tier": "read",
    "fields": ["id", "status"],
}
OPEN_TICKET = {
    "name": "open_ticket",
    "title": "Open an IT ticket",
    "method": "POST",
    "path": "/users/{asker_email}/tickets",
    "params": [
        {"name": "title", "description": "What is wrong"},
        {"name": "urgent", "type": "boolean", "required": False},
    ],
    "tier": "confirm",
}
CLOSE_TICKET = {
    "name": "close_ticket",
    "title": "Close a ticket",
    "method": "DELETE",
    "path": "/tickets/{ticket_id}",
    "params": [{"name": "ticket_id", "type": "integer"}],
    "tier": "approver",
}
PAT = Asker(uuid.uuid4(), "pat@example.com", "Pat")


def _action(raw: dict[str, Any], connection_id: str | None = None) -> HttpAction:
    return HttpAction.model_validate({**raw, "connection_id": connection_id or str(uuid.uuid4())})


# ── checking a call (when the config is saved) ───────────────


@pytest.mark.parametrize(
    ("patch", "said"),
    [
        ({"path": "tickets"}, "starts with /"),
        ({"path": "/x/../admin"}, "stays under"),
        ({"path": "//evil.example.com/x"}, "stays under"),
        ({"path": "/x?all=1"}, "has no query"),
        ({"path": "/x#y"}, "has no query"),
        ({"path": "/tickets/{nope}"}, "no parameter named nope"),
        ({"path": "/tickets/{Ticket}"}, "write a slot"),
        ({"path": "/tickets/{ticket_id"}, "write a slot"),
        ({"path": "/t i"}, "characters"),
        ({"tier": "approver"}, "a GET only reads"),
        ({"fields": ["a b"]}, "isn't a field"),
        (
            {"params": [{"name": "ticket_id", "type": "integer", "required": False}]},
            "make ticket_id required",
        ),
        ({"params": [{"name": "asker_email"}]}, "filled by the platform"),
    ],
)
def test_an_unsafe_call_cant_be_saved(patch: dict[str, Any], said: str) -> None:
    with pytest.raises(ValidationError, match=said):
        _action({**TICKET_STATUS, **patch})


def test_a_change_is_never_a_read() -> None:
    with pytest.raises(ValidationError, match="changes something"):
        _action({**OPEN_TICKET, "tier": "read"})


def test_names_are_unique_across_sql_and_http_actions() -> None:
    cid = str(uuid.uuid4())
    sql = {
        "name": "open_ticket",
        "title": "x",
        "sql": "SELECT 1 AS one",
        "tier": "read",
    }
    with pytest.raises(ValidationError, match="share a name"):
        AssistantConfig.model_validate(
            {
                "databases": [{"connection_id": str(uuid.uuid4()), "actions": [sql]}],
                "tools": {
                    "http_request": {
                        "enabled": True,
                        "actions": [{**OPEN_TICKET, "connection_id": cid}],
                    }
                },
            }
        )


# ── rendering ────────────────────────────────────────────────


def test_values_fill_the_path_one_segment_each_and_the_rest_go_in_the_body() -> None:
    call, values = http_actions.render(
        _action(OPEN_TICKET), {"title": "VPN drops", "urgent": True}, asker=PAT
    )
    assert call.method == "POST"
    assert call.path == "/users/pat%40example.com/tickets", "the asker from the platform"
    assert call.body == {"title": "VPN drops", "urgent": True}
    assert values == {"title": "VPN drops", "urgent": True}


def test_a_get_puts_the_rest_in_the_query() -> None:
    action = _action(
        {
            **TICKET_STATUS,
            "params": [
                *TICKET_STATUS["params"],
                {"name": "verbose", "type": "boolean", "required": False},
            ],
        }
    )
    call, _ = http_actions.render(action, {"ticket_id": 12, "verbose": False}, asker=PAT)
    assert (call.path, call.body) == ("/tickets/12?verbose=false", None)


@pytest.mark.parametrize("value", ["..", ".", "../admin", "a/b", "x?y=1"])
def test_a_value_cant_leave_its_segment(value: str) -> None:
    action = _action(
        {**TICKET_STATUS, "params": [{"name": "ticket_id"}], "path": "/tickets/{ticket_id}"}
    )
    try:
        call, _ = http_actions.render(action, {"ticket_id": value}, asker=PAT)
    except ActionInputError:
        return
    assert call.path.count("/") == 2 and "?" not in call.path, call.path


def test_the_model_cant_name_the_asker_or_extra_values() -> None:
    with pytest.raises(ActionInputError, match="unknown parameter"):
        http_actions.render(
            _action(OPEN_TICKET), {"title": "x", "asker_email": "ceo@example.com"}, asker=PAT
        )


def test_only_the_named_fields_of_an_answer_are_read() -> None:
    body = json.dumps([{"id": 1, "status": "open", "salary": 100000}]).encode()
    out = http_actions.answer(200, "OK", "application/json", body, ["id", "status"])
    assert '"status": "open"' in out and "salary" not in out


# ── the whole thing ──────────────────────────────────────────


class Sent:
    """Stands in for `safe_request`: records each call, answers from a script."""

    def __init__(self, status: int = 200, body: Any = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.status = status
        self.body = body if body is not None else {"id": 7, "status": "open", "owner": "x"}

    async def __call__(self, method: str, url: str, **kw: Any) -> FetchResult:
        self.calls.append({"method": method, "url": url, **kw})
        return FetchResult(
            status=self.status,
            reason="OK",
            url=url,
            headers={"content-type": "application/json"},
            body=json.dumps(self.body).encode(),
            truncated=False,
        )


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> Sent:
    recorder = Sent()
    monkeypatch.setattr(ssrf, "safe_request", recorder)
    return recorder


async def _org(client: AsyncClient) -> tuple[str, dict[str, Actor]]:
    owner = await _register(client, "owner@example.com")
    org = (await client.post(f"{API}/orgs", json={"name": "Acme"}, headers=owner.headers)).json()
    people = {"owner": owner}
    pat = await _register(client, "pat@example.com")
    inv = await client.post(
        f"{API}/orgs/{org['id']}/invites", json={"email": "pat@example.com"}, headers=owner.headers
    )
    token = inv.json()["accept_url"].rsplit("/", 1)[-1]
    assert (await client.post(f"{API}/invites/{token}/accept", headers=pat.headers)).is_success
    people["pat"] = pat
    return org["id"], people


def _h(org_id: str, actor: Actor) -> dict[str, str]:
    return {**actor.headers, "X-Org-Id": org_id}


async def _assistant(
    client: AsyncClient, h: dict[str, str], *, send_asker: bool = True
) -> tuple[str, str]:
    a = (await client.post(f"{API}/assistants", json={"name": "IT desk"}, headers=h)).json()
    conn = await client.post(
        f"{API}/assistants/{a['id']}/http-connections",
        json={
            "name": "Tickets",
            "base_url": BASE,
            "headers": {"Authorization": SECRET},
            "send_asker": send_asker,
        },
        headers=h,
    )
    assert conn.status_code == 201, conn.text
    cid = str(conn.json()["id"])
    cfg = a["draft_config"]
    cfg["tools"]["http_request"] = {
        "enabled": True,
        "allowed_domains": [],
        "actions": [
            {**TICKET_STATUS, "connection_id": cid},
            {**OPEN_TICKET, "connection_id": cid, "tier": "confirm" if send_asker else "approver"},
            {**CLOSE_TICKET, "connection_id": cid},
        ],
    }
    r = await client.put(f"{API}/assistants/{a['id']}/draft-config", json=cfg, headers=h)
    assert r.status_code == 200, r.text
    return str(a["id"]), cid


async def _chat(client: AsyncClient, h: dict[str, str], aid: str, *, preview: bool) -> str:
    body = {"mode": "preview"} if preview else {}
    r = await client.post(f"{API}/assistants/{aid}/conversations", json=body, headers=h)
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def _turn(cid: str, text: str) -> list[Any]:
    events: list[Any] = []
    async with get_sessionmaker()() as session:
        async for event in chat_svc.run_message(session, conversation_id=uuid.UUID(cid), text=text):
            events.append(event)
    return events


async def _requests(cid: str) -> list[Approval]:
    async with get_sessionmaker()() as s:
        return list(
            (
                await s.scalars(select(Approval).where(Approval.conversation_id == uuid.UUID(cid)))
            ).all()
        )


async def test_header_values_are_write_only(client: AsyncClient) -> None:
    org_id, people = await _org(client)
    h = _h(org_id, people["owner"])
    aid, cid = await _assistant(client, h)

    (listed,) = (await client.get(f"{API}/assistants/{aid}/http-connections", headers=h)).json()
    assert listed["header_names"] == ["Authorization"]
    assert SECRET not in json.dumps(listed)
    async with get_sessionmaker()() as s:
        row = await s.get(HttpConnection, uuid.UUID(cid))
        assert row is not None and row.headers_secret_ref is not None
        logged = (await s.scalars(select(AuditLog).where(AuditLog.target_id == cid))).all()
    assert [e.action for e in logged] == ["http_connection.create"]
    assert SECRET not in json.dumps([e.meta for e in logged])


@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        ({"base_url": "https://user:pw@tickets.example.com"}, 400, "invalid_url"),
        ({"base_url": "https://tickets.example.com/api?key=1"}, 400, "invalid_url"),
        ({"base_url": "ftp://tickets.example.com"}, 400, "invalid_url"),
        ({"headers": {"X-Asker-Email": "ceo@example.com"}}, 422, None),
        ({"headers": {"Host": "elsewhere"}}, 422, None),
        ({"headers": {"X-Key": "a\r\nInjected: 1"}}, 422, None),
    ],
)
async def test_a_connection_is_checked(
    client: AsyncClient, body: dict[str, Any], status: int, code: str | None
) -> None:
    org_id, people = await _org(client)
    h = _h(org_id, people["owner"])
    a = (await client.post(f"{API}/assistants", json={"name": "x"}, headers=h)).json()
    r = await client.post(
        f"{API}/assistants/{a['id']}/http-connections",
        json={"name": "T", "base_url": "https://tickets.example.com", **body},
        headers=h,
    )
    assert r.status_code == status, r.text
    if code:
        assert r.json()["error"]["code"] == code


async def test_a_read_call_runs_at_once_with_the_connections_headers(
    client: AsyncClient, sent: Sent
) -> None:
    org_id, people = await _org(client)
    h = _h(org_id, people["owner"])
    aid, _ = await _assistant(client, h)
    events = await _turn(
        await _chat(client, h, aid, preview=True), 'act: ticket_status {"ticket_id": 7}'
    )

    (result,) = [e for e in events if e.type == "tool_result"]
    assert result.status == "success", result.output
    assert '"status": "open"' in result.output and "owner" not in result.output, "only the fields"
    (call,) = sent.calls
    assert (call["method"], call["url"]) == ("GET", f"{BASE}/tickets/7")
    assert call["headers"]["Authorization"] == SECRET
    assert call["headers"]["X-Asker-Email"] == "owner@example.com"
    assert call["allowed_domains"] == ["tickets.corp.example.com"], "this host only"
    assert call["limits"].max_redirects == 0, "secret headers never follow a redirect"
    assert SECRET not in json.dumps([getattr(e, "__dict__", str(e)) for e in events], default=str)


async def test_a_confirmed_call_waits_for_the_asker_then_sends_what_was_shown(
    client: AsyncClient, sent: Sent
) -> None:
    org_id, people = await _org(client)
    h = _h(org_id, people["owner"])
    aid, _ = await _assistant(client, h)
    await go_live(client, aid, h)
    ph = _h(org_id, people["pat"])
    cid = await _chat(client, ph, aid, preview=False)
    events = await _turn(cid, 'act: open_ticket {"title": "VPN drops", "urgent": true}')

    (prompt,) = [e for e in events if e.type == "approval_required"]
    assert (prompt.tier, prompt.title, prompt.action_name) == (
        "asker",
        "Open an IT ticket",
        "open_ticket",
    )
    assert sent.calls == [], "nothing is sent before the asker confirms"
    (row,) = await _requests(cid)
    assert SECRET not in (row.rationale or "") + json.dumps(row.input)

    other = await decide(client, h, prompt.approval_id, "approved")
    assert (other.status_code, other.json()["error"]["code"]) == (403, "asker_decides")
    r = await decide(
        client, ph, prompt.approval_id, "approved", params={"title": "VPN keeps dropping"}
    )
    assert r.status_code == 200, r.text
    await executor.drain()

    (call,) = sent.calls
    assert (call["method"], call["url"]) == ("POST", f"{BASE}/users/pat%40example.com/tickets")
    assert json.loads(call["body"]) == {"title": "VPN keeps dropping"}, "the corrected value"
    assert call["headers"]["X-Asker-Email"] == "pat@example.com"
    assert call["headers"]["Authorization"] == SECRET
    (row,) = await _requests(cid)
    assert row.status is ApprovalStatus.succeeded


async def test_an_approver_decides_and_a_moved_connection_is_not_followed(
    client: AsyncClient, sent: Sent
) -> None:
    org_id, people = await _org(client)
    h = _h(org_id, people["owner"])
    aid, conn_id = await _assistant(client, h)
    await go_live(client, aid, h)
    ph = _h(org_id, people["pat"])
    cid = await _chat(client, ph, aid, preview=False)
    events = await _turn(cid, 'act: close_ticket {"ticket_id": 9}')
    (prompt,) = [e for e in events if e.type == "approval_required"]
    assert prompt.tier == "approver"
    (queued,) = (await client.get(f"{API}/approvals/decidable", headers=h)).json()
    assert queued["rationale"] == f"DELETE {BASE}/tickets/9"

    moved = await client.patch(
        f"{API}/assistants/{aid}/http-connections/{conn_id}",
        json={"base_url": "https://elsewhere.example.com", "headers": {"Authorization": "x"}},
        headers=h,
    )
    assert moved.status_code == 200, moved.text
    assert (await decide(client, h, prompt.approval_id, "approved")).status_code == 200
    await executor.drain()
    assert sent.calls == [], "the address changed after it was asked"
    (row,) = await _requests(cid)
    assert row.status is ApprovalStatus.failed


async def test_a_confirmed_call_needs_the_connection_to_say_who_asks(
    client: AsyncClient, sent: Sent
) -> None:
    org_id, people = await _org(client)
    h = _h(org_id, people["owner"])
    aid, conn_id = await _assistant(client, h, send_asker=False)

    check = await client.post(
        f"{API}/assistants/{aid}/actions:check",
        json={
            "connection_id": conn_id,
            "kind": "http",
            "action": {**OPEN_TICKET, "connection_id": conn_id},
        },
        headers=h,
    )
    assert check.status_code == 200 and not check.json()["ok"]
    assert "who is asking" in check.json()["error"]
    unknown = await client.post(
        f"{API}/assistants/{aid}/actions:check",
        json={
            "connection_id": "x",
            "kind": "http",
            "action": {**OPEN_TICKET, "connection_id": str(uuid.uuid4())},
        },
        headers=h,
    )
    assert "HTTP connections" in unknown.json()["error"]

    # A confirm call slipped into the draft is refused when asked for.
    a = (await client.get(f"{API}/assistants/{aid}", headers=h)).json()
    cfg = a["draft_config"]
    cfg["tools"]["http_request"]["actions"][1]["tier"] = "confirm"
    assert (
        await client.put(f"{API}/assistants/{aid}/draft-config", json=cfg, headers=h)
    ).status_code == 200
    cid = await _chat(client, h, aid, preview=True)
    events = await _turn(cid, 'act: open_ticket {"title": "x"}')
    (result,) = [e for e in events if e.type == "tool_result"]
    assert "who is asking" in result.output
    assert await _requests(cid) == [] and sent.calls == []

    # And the system isn't told who is asking.
    events = await _turn(cid, 'act: ticket_status {"ticket_id": 3}')
    (call,) = sent.calls
    assert call["headers"]["Authorization"] == SECRET
    assert not any(k.lower().startswith("x-asker") for k in call["headers"])


async def test_a_changed_connection_is_dropped_from_the_approved_version(
    client: AsyncClient,
) -> None:
    org_id, people = await _org(client)
    h = _h(org_id, people["owner"])
    aid, conn_id = await _assistant(client, h)
    async with get_sessionmaker()() as s:
        a = (await client.get(f"{API}/assistants/{aid}", headers=h)).json()
        config = AssistantConfig.model_validate(a["draft_config"])
        frozen = await manifest.build(s, uuid.UUID(aid), config)
    assert [c["id"] for c in frozen["http_connections"]] == [conn_id]

    await client.patch(
        f"{API}/assistants/{aid}/http-connections/{conn_id}",
        json={"send_asker": False},
        headers=h,
    )
    async with get_sessionmaker()() as s:
        drift = await manifest.check(s, uuid.UUID(aid), frozen)
    assert [(d.kind, d.what) for d in drift] == [("http_connection", "changed")]
    assert manifest.apply(config, frozen, drift).tools.http_request.actions == []


async def test_with_calls_and_no_sites_only_the_calls_are_offered() -> None:
    from app.agent.options import _enabled_caps

    cid = str(uuid.uuid4())
    config = AssistantConfig.model_validate(
        {
            "tools": {
                "http_request": {
                    "enabled": True,
                    "actions": [{**TICKET_STATUS, "connection_id": cid}],
                }
            }
        }
    )
    names = [c.name for c in _enabled_caps(config, uuid.uuid4(), asker=PAT)]
    assert names == ["act_ticket_status"], "no free-form tool that refuses everything"
    config.tools.http_request.allowed_domains = ["api.example.com"]
    names = [c.name for c in _enabled_caps(config, uuid.uuid4(), asker=PAT)]
    assert sorted(names) == ["act_ticket_status", "http_request"]


def test_calls_without_sites_are_not_a_broken_tool() -> None:
    """The "no allowed sites" warning blocks approval (7b.1). With declared
    calls it doesn't apply: the calls are what the tool sends."""
    from app.graph.project import project_config
    from app.graph.validate import validate_graph

    cid = str(uuid.uuid4())
    calls = AssistantConfig.model_validate(
        {
            "tools": {
                "http_request": {
                    "enabled": True,
                    "actions": [{**TICKET_STATUS, "connection_id": cid}],
                }
            }
        }
    )
    codes = [w.code for w in validate_graph(project_config(calls)).warnings]
    assert "http_no_allowed_sites" not in codes
    calls.tools.http_request.actions = []
    codes = [w.code for w in validate_graph(project_config(calls)).warnings]
    assert "http_no_allowed_sites" in codes
