"""Declared actions (Phase 9.1, plan D7 and D8).

A named statement the builder writes on a database node, with parameters
the model fills and `:asker_*` slots the platform fills. Its tier says how
it runs: at once (a read), once the asker confirms (only for a statement
pinned to their own rows), or once an approver decides. These run against a
real SQLite helpdesk database, through the fake driver's `act:` command.
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from typing import Any

import pytest
from app.agent import actions
from app.agent.actions import ActionInputError, Asker
from app.db.session import get_sessionmaker
from app.models.approval import Approval, ApprovalStatus
from app.schemas.assistant_config import DeclaredAction
from app.services import chat as chat_svc
from app.services import executor
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import select
from tests.pending import decide
from tests.versions import go_live

from test_orgs import Actor, _register  # type: ignore[import-not-found]

pytestmark = pytest.mark.anyio

API = "/api/v1"

MY_TICKETS = {
    "name": "my_tickets",
    "title": "Look up my tickets",
    "sql": "SELECT id, title, status FROM tickets WHERE requester_email = :asker_email",
    "tier": "read",
}
REQUEST_ACCESS = {
    "name": "request_access",
    "title": "Request access to an app",
    "description": "When someone asks for software we license.",
    "sql": "INSERT INTO access_requests (app, reason, requester_email) "
    "VALUES (:app, :reason, :asker_email)",
    "params": [
        {"name": "app", "choices": ["Figma", "Slack", "Jira"], "description": "The app"},
        {"name": "reason", "required": False, "description": "Why"},
    ],
    "tier": "confirm",
}
CLOSE_TICKET = {
    "name": "close_ticket",
    "title": "Close a ticket",
    "sql": "UPDATE tickets SET status = 'closed' WHERE id = :id",
    "params": [{"name": "id", "type": "integer", "description": "The ticket"}],
    "tier": "approver",
}


# ── checking a template ──────────────────────────────────────


@pytest.mark.parametrize(
    ("action", "said"),
    [
        (
            {**CLOSE_TICKET, "tier": "confirm"},
            "'confirm' is only for the asker's own rows",
        ),
        (
            {
                **CLOSE_TICKET,
                "sql": "UPDATE tickets SET status = 'closed' WHERE id = :id "
                "OR requester_email = :asker_email",
                "tier": "confirm",
            },
            "'confirm' is only for the asker's own rows",
        ),
        (
            {
                **REQUEST_ACCESS,
                "sql": "INSERT INTO access_requests (app, reason, requester_email) VALUES "
                "(:app, :reason, :asker_email) ON CONFLICT (app) DO UPDATE SET reason = :reason",
            },
            "'confirm' is only for the asker's own rows",
        ),
        ({**MY_TICKETS, "tier": "approver"}, "it only reads, so its tier is 'read'"),
        ({**CLOSE_TICKET, "tier": "read"}, "it changes data"),
        ({"name": "drop", "title": "x", "sql": "DROP TABLE tickets"}, "structure"),
        (
            {"name": "two", "title": "x", "sql": "SELECT 1; SELECT 2", "tier": "read"},
            "one statement",
        ),
        (
            {"name": "q", "title": "x", "sql": "SELECT * FROM t WHERE id = ?", "tier": "read"},
            "named slots",
        ),
        ({**MY_TICKETS, "sql": MY_TICKETS["sql"] + " AND id = :id"}, "no parameter named :id"),
        (
            {**CLOSE_TICKET, "params": [*CLOSE_TICKET["params"], {"name": "extra"}]},
            "never uses extra",
        ),
        ({**CLOSE_TICKET, "params": [{"name": "asker_email"}]}, "filled by the platform"),
        (
            {
                "name": "f",
                "title": "x",
                "sql": "SELECT pg_read_file('/etc/passwd')",
                "tier": "read",
            },
            "SQL guard refuses it",
        ),
    ],
)
def test_an_unsafe_action_cant_be_saved(action: dict[str, Any], said: str) -> None:
    with pytest.raises(ValidationError, match=said):
        DeclaredAction.model_validate(action)


REOPEN_MINE = {
    "name": "reopen_mine",
    "title": "Reopen my ticket",
    "sql": "UPDATE tickets SET status = 'open' WHERE id = :id AND requester_email = :asker_email",
    "params": [{"name": "id", "type": "integer"}],
    "tier": "confirm",
}


@pytest.mark.parametrize("action", [MY_TICKETS, REQUEST_ACCESS, CLOSE_TICKET, REOPEN_MINE])
def test_a_sound_action_is_accepted(action: dict[str, Any]) -> None:
    assert DeclaredAction.model_validate(action).name == action["name"]


def test_two_actions_cant_share_a_name() -> None:
    from app.schemas.assistant_config import AssistantConfig

    with pytest.raises(ValidationError, match="two actions share a name"):
        AssistantConfig.model_validate(
            {
                "databases": [
                    {"connection_id": "a", "actions": [CLOSE_TICKET]},
                    {"connection_id": "b", "actions": [CLOSE_TICKET]},
                ]
            }
        )


# ── rendering ────────────────────────────────────────────────

ASKER = Asker(user_id=uuid.uuid4(), email="o'brien@example.com", name="Pat O'Brien")


@pytest.mark.parametrize("engine", ["postgres", "mysql", "sqlite"])
def test_values_never_change_the_statement(engine: str) -> None:
    action = DeclaredAction.model_validate(REQUEST_ACCESS)
    evil = "x'); DROP TABLE tickets; -- \\' \\\\"
    statement, values = actions.render(
        action, {"app": "Figma", "reason": evil}, asker=ASKER, engine=engine
    )
    assert values == {"app": "Figma", "reason": evil}
    assert statement.upper().startswith("INSERT INTO ACCESS_REQUESTS")
    assert statement.count(";") <= evil.count(";"), "only inside the quoted value"
    import sqlglot

    (tree,) = [t for t in sqlglot.parse(statement, read=engine) if t is not None]
    texts = sorted(lit.this for lit in tree.find_all(sqlglot.exp.Literal) if lit.is_string)
    assert texts == sorted(["Figma", evil, "o'brien@example.com"])


def test_the_asker_comes_from_the_platform_not_the_model() -> None:
    action = DeclaredAction.model_validate(REQUEST_ACCESS)
    with pytest.raises(ActionInputError, match="unknown parameter: asker_email"):
        actions.render(
            action, {"app": "Figma", "asker_email": "ceo@example.com"}, asker=ASKER, engine="sqlite"
        )


@pytest.mark.parametrize(
    ("args", "said"),
    [
        ({"app": "Photoshop"}, "must be one of"),
        ({}, "app is required"),
        ({"app": "Figma", "reason": "a\x00b"}, "control characters"),
        ({"app": "Figma", "reason": "x" * 501}, "longer than 500"),
    ],
)
def test_values_are_checked_against_the_action(args: dict[str, Any], said: str) -> None:
    action = DeclaredAction.model_validate(REQUEST_ACCESS)
    with pytest.raises(ActionInputError, match=said):
        actions.render(action, args, asker=ASKER, engine="sqlite")


@pytest.mark.parametrize(
    ("raw", "ok"), [(7, True), ("42", True), ("7.5", False), (True, False), ("1; DROP", False)]
)
def test_a_whole_number_is_a_whole_number(raw: Any, ok: bool) -> None:
    action = DeclaredAction.model_validate(CLOSE_TICKET)
    if ok:
        statement, _ = actions.render(action, {"id": raw}, asker=ASKER, engine="sqlite")
        assert statement.endswith(f"WHERE id = {int(raw)}")
    else:
        with pytest.raises(ActionInputError, match="whole number"):
            actions.render(action, {"id": raw}, asker=ASKER, engine="sqlite")


# ── in a conversation ────────────────────────────────────────


def _helpdesk(path: Path) -> Path:
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE tickets (id INTEGER PRIMARY KEY, title TEXT, status TEXT,
                              requester_email TEXT);
        CREATE TABLE access_requests (id INTEGER PRIMARY KEY, app TEXT, reason TEXT,
                                      requester_email TEXT);
        INSERT INTO tickets VALUES (1, 'Laptop will not start', 'open', 'owner@example.com');
        INSERT INTO tickets VALUES (2, 'VPN drops', 'open', 'someone@example.com');
        """
    )
    db.commit()
    db.close()
    return path


async def _org(client: AsyncClient) -> tuple[str, dict[str, Actor]]:
    owner = await _register(client, "owner@example.com")
    org = (await client.post(f"{API}/orgs", json={"name": "Acme"}, headers=owner.headers)).json()
    people = {"owner": owner}
    for name in ("pat",):
        actor = await _register(client, f"{name}@example.com")
        inv = await client.post(
            f"{API}/orgs/{org['id']}/invites",
            json={"email": f"{name}@example.com"},
            headers=owner.headers,
        )
        token = inv.json()["accept_url"].rsplit("/", 1)[-1]
        assert (
            await client.post(f"{API}/invites/{token}/accept", headers=actor.headers)
        ).is_success
        people[name] = actor
    return org["id"], people


def _h(org_id: str, actor: Actor) -> dict[str, str]:
    return {**actor.headers, "X-Org-Id": org_id}


async def _assistant(
    client: AsyncClient, h: dict[str, str], db: Path, *, write: bool = True
) -> tuple[str, str]:
    a = (await client.post(f"{API}/assistants", json={"name": "Helpdesk"}, headers=h)).json()
    conn = await client.post(
        f"{API}/assistants/{a['id']}/db-connections",
        json={
            "name": "Helpdesk",
            "engine": "sqlite",
            "database": str(db),
            "permissions": {"read": True, "write": write},
        },
        headers=h,
    )
    assert conn.status_code == 201, conn.text
    cfg = a["draft_config"]
    cfg["databases"] = [
        {
            "connection_id": conn.json()["id"],
            "nl2sql": False,
            "expose_write": False,
            "actions": [MY_TICKETS, REQUEST_ACCESS, CLOSE_TICKET],
        }
    ]
    r = await client.put(f"{API}/assistants/{a['id']}/draft-config", json=cfg, headers=h)
    assert r.status_code == 200, r.text
    return str(a["id"]), str(conn.json()["id"])


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


def _rows(db: Path, sql: str) -> list[tuple[Any, ...]]:
    con = sqlite3.connect(db)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


async def test_a_read_action_runs_at_once_as_the_asker(client: AsyncClient, tmp_path: Path) -> None:
    db = _helpdesk(tmp_path / "helpdesk.db")
    org_id, people = await _org(client)
    h = _h(org_id, people["owner"])
    aid, _ = await _assistant(client, h, db)
    events = await _turn(await _chat(client, h, aid, preview=True), "act: my_tickets")

    (result,) = [e for e in events if e.type == "tool_result"]
    assert result.status == "success"
    assert "Laptop will not start" in result.output
    assert "VPN drops" not in result.output, "only the asker's own tickets"
    assert "approval_required" not in [e.type for e in events]


async def test_the_asker_confirms_their_own_and_may_correct_it(
    client: AsyncClient, tmp_path: Path
) -> None:
    db = _helpdesk(tmp_path / "helpdesk.db")
    org_id, people = await _org(client)
    h = _h(org_id, people["owner"])
    aid, _ = await _assistant(client, h, db)
    await go_live(client, aid, h)
    ph = _h(org_id, people["pat"])
    cid = await _chat(client, ph, aid, preview=False)
    events = await _turn(cid, 'act: request_access {"app": "Figma", "reason": "Redesign"}')

    (prompt,) = [e for e in events if e.type == "approval_required"]
    assert (prompt.tier, prompt.title, prompt.action_name) == (
        "asker",
        "Request access to an app",
        "request_access",
    )
    assert _rows(db, "SELECT count(*) FROM access_requests") == [(0,)], "not before confirming"

    # Pat's card: their own values, editable, and no statement (D19).
    (card,) = (await client.get(f"{API}/conversations/{cid}/approvals", headers=ph)).json()["items"]
    assert card["input"] == {"app": "Figma", "reason": "Redesign"}
    assert card["rationale"] is None
    assert [f["name"] for f in card["fields"]] == ["app", "reason"]
    assert card["fields"][0]["choices"] == ["Figma", "Slack", "Jira"]

    # Someone else can't confirm Pat's, an admin included.
    other = await decide(client, h, prompt.approval_id, "approved")
    assert (other.status_code, other.json()["error"]["code"]) == (403, "asker_decides")

    bad = await decide(client, ph, prompt.approval_id, "approved", params={"app": "Photoshop"})
    assert (bad.status_code, bad.json()["error"]["code"]) == (400, "invalid_values")
    r = await decide(client, ph, prompt.approval_id, "approved", params={"app": "Slack"})
    assert r.status_code == 200, r.text
    await executor.drain()
    assert _rows(db, "SELECT app, reason, requester_email FROM access_requests") == [
        ("Slack", None, "pat@example.com")
    ], "the corrected value, the asker's own address"
    (row,) = await _requests(cid)
    assert row.status is ApprovalStatus.succeeded and row.input == {"app": "Slack", "reason": None}


async def test_values_that_dont_fit_store_nothing(client: AsyncClient, tmp_path: Path) -> None:
    db = _helpdesk(tmp_path / "helpdesk.db")
    org_id, people = await _org(client)
    h = _h(org_id, people["owner"])
    aid, _ = await _assistant(client, h, db)
    cid = await _chat(client, h, aid, preview=True)

    events = await _turn(cid, 'act: request_access {"app": "Photoshop"}')
    (result,) = [e for e in events if e.type == "tool_result"]
    # Refused by the tool's schema (the model is told the choices) or by
    # the action's own check: either way nothing is stored.
    assert "must be one of" in result.output or "is not one of" in result.output
    events = await _turn(
        cid, 'act: request_access {"app": "Figma", "asker_email": "ceo@example.com"}'
    )
    (result,) = [e for e in events if e.type == "tool_result"]
    assert "unknown parameter" in result.output or "was unexpected" in result.output
    assert await _requests(cid) == []


async def test_an_approver_action_waits_for_an_approver(
    client: AsyncClient, tmp_path: Path
) -> None:
    db = _helpdesk(tmp_path / "helpdesk.db")
    org_id, people = await _org(client)
    h = _h(org_id, people["owner"])
    aid, _ = await _assistant(client, h, db)
    await go_live(client, aid, h)
    ph = _h(org_id, people["pat"])
    cid = await _chat(client, ph, aid, preview=False)
    events = await _turn(cid, 'act: close_ticket {"id": 2}')
    (prompt,) = [e for e in events if e.type == "approval_required"]
    assert prompt.tier == "approver"

    mine = await decide(client, ph, prompt.approval_id, "approved")
    assert (mine.status_code, mine.json()["error"]["code"]) == (403, "cannot_decide_own")
    (queued,) = (await client.get(f"{API}/approvals/decidable", headers=h)).json()
    assert queued["rationale"] == "UPDATE tickets SET status = 'closed' WHERE id = 2"
    assert (queued["asker_email"], queued["assistant_name"]) == ("pat@example.com", "Helpdesk")

    assert (await decide(client, h, prompt.approval_id, "approved")).status_code == 200
    await executor.drain()
    assert _rows(db, "SELECT status FROM tickets WHERE id = 2") == [("closed",)]


async def test_a_write_on_a_read_only_connection_is_refused_up_front(
    client: AsyncClient, tmp_path: Path
) -> None:
    db = _helpdesk(tmp_path / "helpdesk.db")
    org_id, people = await _org(client)
    h = _h(org_id, people["owner"])
    aid, conn = await _assistant(client, h, db, write=False)

    detail = (await client.get(f"{API}/assistants/{aid}", headers=h)).json()
    codes = [i["code"] for i in detail["draft_validation"]["errors"]]
    assert "action_without_write" in codes, "it can't be submitted like this"

    cid = await _chat(client, h, aid, preview=True)
    events = await _turn(cid, 'act: request_access {"app": "Figma"}')
    (result,) = [e for e in events if e.type == "tool_result"]
    assert "read-only" in result.output
    assert await _requests(cid) == []

    check = await client.post(
        f"{API}/assistants/{aid}/actions:check",
        json={"connection_id": conn, "action": CLOSE_TICKET},
        headers=h,
    )
    assert check.json() == {
        "ok": True,
        "error": None,
        "writes": True,
        "connection_can_write": False,
    }


async def test_the_builder_checks_an_action_before_saving_it(
    client: AsyncClient, tmp_path: Path
) -> None:
    db = _helpdesk(tmp_path / "helpdesk.db")
    org_id, people = await _org(client)
    h = _h(org_id, people["owner"])
    aid, conn = await _assistant(client, h, db)
    r = await client.post(
        f"{API}/assistants/{aid}/actions:check",
        json={"connection_id": conn, "action": {**CLOSE_TICKET, "tier": "confirm"}},
        headers=h,
    )
    out = r.json()
    assert out["ok"] is False and "asker's own rows" in out["error"]
    pat = await client.post(
        f"{API}/assistants/{aid}/actions:check",
        json={"connection_id": conn, "action": CLOSE_TICKET},
        headers=_h(org_id, people["pat"]),
    )
    assert pat.status_code == 403, "an employee builds nothing"


async def test_actions_survive_the_canvas_and_are_named_on_the_card(
    client: AsyncClient, tmp_path: Path
) -> None:
    db = _helpdesk(tmp_path / "helpdesk.db")
    org_id, people = await _org(client)
    h = _h(org_id, people["owner"])
    aid, _ = await _assistant(client, h, db)
    detail = (await client.get(f"{API}/assistants/{aid}", headers=h)).json()
    (node,) = [n for n in detail["draft_graph"]["nodes"] if n["type"] == "database"]
    assert [a["name"] for a in node["data"]["actions"]] == [
        "my_tickets",
        "request_access",
        "close_ticket",
    ]
    again = await client.put(
        f"{API}/assistants/{aid}/draft-graph", json=detail["draft_graph"], headers=h
    )
    assert again.status_code == 200, again.text
    names = [a["name"] for a in again.json()["config"]["databases"][0]["actions"]]
    assert names == ["my_tickets", "request_access", "close_ticket"]

    await go_live(client, aid, h)
    about = (await client.get(f"{API}/chat/assistants/{aid}/about", headers=h)).json()
    said = str(about)
    assert "Look up my tickets" in said and "Close a ticket" in said
