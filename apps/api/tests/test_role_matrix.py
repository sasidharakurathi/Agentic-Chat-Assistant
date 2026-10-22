"""Who may call what inside one org, route by route (Phase 7a.3).

`test_tenant_isolation.py` walks every route as someone from another org.
This is its in-org twin: one org with an owner, an admin, a member who
built the assistant (the creator) and a member who did not. `ROLE_MATRIX`
says, for every route the API publishes, which of them may call it; the
walk asks each route as each of them and checks the answer.

Three promises:

- **The table is complete.** A route added without an entry here fails,
  so nobody has to remember to decide who may call it.
- **The table is true.** Each refusal it promises is a 403. Each access it
  promises gets past the checks: a read answers, and a write with a body
  is turned away only for the body (422), which proves it got past the
  access checks without changing anything. Writes with no body are not
  sent as an allowed role (they would delete the walk's own objects); the
  other test files cover them.
- **Deny by default.** With the Studio floor raised above `member`, a
  member stands in for a role that does not exist yet (the Employee role,
  Phase 8). Every route outside `PUBLIC` and `SIGNED_IN` must then refuse
  them with `studio_access_required`. A new route that loads an org's data
  without the floor fails here.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from app import queue
from app.db.session import get_sessionmaker
from app.evals import runner
from app.main import app
from app.models.attachment import Attachment
from app.models.conversation import ConversationShare
from app.models.enums import MemberRole
from app.models.integration import HttpConnection, McpServer, McpTransport
from app.security import access
from httpx import AsyncClient
from tests.pending import waiting_request
from tests.versions import go_live

from test_orgs import Actor, _register  # type: ignore[import-not-found]

pytestmark = pytest.mark.anyio

API = "/api/v1"
METHODS = ("get", "post", "put", "patch", "delete")

# ── the matrix ───────────────────────────────────────────────

#: No sign-in at all.
PUBLIC = "public"
#: Signed in; about the caller, not about any org. A role below the floor
#: still reaches these. `POST /orgs` is one: with `ORG_MODE=multi` a person
#: can start their own org; in single-org mode it is refused (Phase 7b.8).
SIGNED_IN = "signed-in"
#: Any member of the org at or above the Studio floor.
MEMBER = "member"
#: The assistant's creator, or an admin or owner.
EDITOR = "editor"
#: The person who started the conversation, and nobody else: admins read
#: other people's conversations but cannot act in them (Phase 7a.4).
STARTER = "starter"
#: Deciding a request (Phase 9.1, plan D7): one of the assistant's
#: approvers (admins and the owner while it names nobody), never the person
#: who asked. The walk's request is the creator's, in live Chat.
DECIDER = "decider"
#: One request (Phase 9.2): the person who asked, whoever may decide it or
#: decided it, and admins. The walk's request is the creator's.
REQUEST = "request"
#: Admins and owners.
ADMIN = "admin"
#: The owner alone.
OWNER = "owner"
#: Reading a conversation (Phase 7b.2): the person who started it, or an
#: admin or owner, who gives a reason that is recorded. The walk sends one.
READER = "reader"
#: Chat (Phase 8.1): every member, the Employee role included. An
#: assistant-scoped Chat route answers an employee only for an assistant
#: that answers in Chat.
CHAT = "chat"
#: Reading a conversation from Chat: as READER, and the starter may be an
#: employee.
CHAT_READER = "chat-reader"
#: Acting in a conversation from Chat: as STARTER, employees included.
CHAT_STARTER = "chat-starter"

ROLE_MATRIX: dict[str, str] = {
    # Health and sign-in.
    "GET /healthz": PUBLIC,
    "GET /readyz": PUBLIC,
    "POST /api/v1/auth/register": PUBLIC,
    "POST /api/v1/auth/login": PUBLIC,
    "POST /api/v1/auth/refresh": PUBLIC,
    "POST /api/v1/auth/logout": PUBLIC,
    # Getting back in (Phase 7b.6): the link or the password is the proof.
    "POST /api/v1/auth/password-reset": PUBLIC,
    "POST /api/v1/auth/password-reset/confirm": PUBLIC,
    "POST /api/v1/auth/verify-email": PUBLIC,
    "GET /api/v1/invites/{token}": PUBLIC,
    "GET /api/v1/meta/config-schema": PUBLIC,
    "GET /api/v1/meta/graph-schema": PUBLIC,
    "GET /api/v1/meta/samples": PUBLIC,
    # Chat's own (Phase 8.1): the catalog, a card, your conversations.
    "GET /api/v1/chat/assistants": CHAT,
    "GET /api/v1/chat/assistants/{assistant_id}": CHAT,
    "GET /api/v1/chat/conversations": CHAT,
    "GET /api/v1/chat/help": CHAT,
    "GET /api/v1/chat/notice": CHAT,
    "POST /api/v1/chat/notice:accept": CHAT,
    "GET /api/v1/chat/access": CHAT,
    "GET /api/v1/chat/assistants/{assistant_id}/about": CHAT,
    # The admins' queue of requests waiting (an employee's wait for them).
    # Google sign-in (Phase 8.3): the flow is the browser's, before any
    # sign-in; connecting and disconnecting are the signed-in person's.
    "GET /api/v1/auth/providers": PUBLIC,
    "GET /api/v1/auth/google/start": PUBLIC,
    "GET /api/v1/auth/google/callback": PUBLIC,
    "POST /api/v1/auth/google/exchange": PUBLIC,
    "POST /api/v1/auth/google/link-ticket": SIGNED_IN,
    "DELETE /api/v1/auth/google": SIGNED_IN,
    # Two-step sign-in (Phase 8.4): the second step of a sign-in, and the
    # signed-in person's own setup.
    "POST /api/v1/auth/login/two-factor": PUBLIC,
    "POST /api/v1/auth/device-code": SIGNED_IN,
    "POST /api/v1/auth/device-code/redeem": PUBLIC,
    "GET /api/v1/auth/two-factor": SIGNED_IN,
    "POST /api/v1/auth/two-factor/setup": SIGNED_IN,
    "POST /api/v1/auth/two-factor/confirm": SIGNED_IN,
    "POST /api/v1/auth/two-factor/verify": SIGNED_IN,
    "POST /api/v1/auth/two-factor/recovery-codes": SIGNED_IN,
    "POST /api/v1/auth/two-factor/disable": SIGNED_IN,
    # Audiences and the catalog (Phase 8.5).
    "GET /api/v1/assistants/{assistant_id}/listing": MEMBER,
    "GET /api/v1/assistants/{assistant_id}/feedback": EDITOR,
    "PUT /api/v1/assistants/{assistant_id}/listing": EDITOR,
    "POST /api/v1/assistants/{assistant_id}:official": ADMIN,
    "GET /api/v1/orgs/{org_id}/groups": MEMBER,
    "POST /api/v1/orgs/{org_id}/groups": ADMIN,
    "PATCH /api/v1/orgs/{org_id}/groups/{group_id}": ADMIN,
    "DELETE /api/v1/orgs/{org_id}/groups/{group_id}": ADMIN,
    "GET /api/v1/orgs/{org_id}/groups/{group_id}/members": ADMIN,
    "POST /api/v1/orgs/{org_id}/groups/{group_id}/members": ADMIN,
    "DELETE /api/v1/orgs/{org_id}/groups/{group_id}/members/{user_id}": ADMIN,
    "GET /api/v1/auth/me": SIGNED_IN,
    "POST /api/v1/auth/password": SIGNED_IN,
    "POST /api/v1/auth/verify-email/send": SIGNED_IN,
    "GET /api/v1/orgs": SIGNED_IN,
    "POST /api/v1/orgs": SIGNED_IN,
    "POST /api/v1/invites/{token}/accept": SIGNED_IN,
    # The org.
    "GET /api/v1/orgs/{org_id}/members": MEMBER,
    "PATCH /api/v1/orgs/{org_id}/members/{user_id}": ADMIN,
    "POST /api/v1/orgs/{org_id}/invites": ADMIN,
    # People leaving and coming back, and invites (Phase 7b.4).
    "GET /api/v1/orgs/{org_id}/invites": ADMIN,
    # Email (Phase 7b.5): how it is sent, and a test to yourself.
    "GET /api/v1/orgs/{org_id}/mail": ADMIN,
    "GET /api/v1/orgs/{org_id}/settings": ADMIN,
    "PATCH /api/v1/orgs/{org_id}/settings": OWNER,
    "GET /api/v1/orgs/{org_id}/budgets/people": ADMIN,
    "PUT /api/v1/orgs/{org_id}/budgets/people": ADMIN,
    "PUT /api/v1/orgs/{org_id}/members/{user_id}/budget": ADMIN,
    "GET /api/v1/orgs/{org_id}/alerts": ADMIN,
    "POST /api/v1/orgs/{org_id}/alerts:resolve": ADMIN,
    "PUT /api/v1/orgs/{org_id}/retention": ADMIN,
    "POST /api/v1/orgs/{org_id}/members/{user_id}:erase": OWNER,
    "GET /api/v1/orgs/{org_id}/holds": ADMIN,
    "POST /api/v1/orgs/{org_id}/holds": OWNER,
    "POST /api/v1/orgs/{org_id}/holds:release": OWNER,
    "GET /api/v1/orgs/{org_id}/erasures": ADMIN,
    "GET /api/v1/orgs/{org_id}/notice": ADMIN,
    "PUT /api/v1/orgs/{org_id}/notice": OWNER,
    "GET /api/v1/orgs/{org_id}/notice/revisions": ADMIN,
    "POST /api/v1/orgs/{org_id}/mail:test": ADMIN,
    "DELETE /api/v1/orgs/{org_id}/invites/{invite_id}": ADMIN,
    "GET /api/v1/orgs/{org_id}/members/{user_id}/leaving": ADMIN,
    "POST /api/v1/orgs/{org_id}/members/{user_id}:deactivate": ADMIN,
    "POST /api/v1/orgs/{org_id}/members/{user_id}:reactivate": ADMIN,
    "GET /api/v1/orgs/{org_id}/usage": MEMBER,
    "GET /api/v1/orgs/{org_id}/audit-log": ADMIN,
    # The audit export and its integrity check (Phase 7b.7).
    "GET /api/v1/orgs/{org_id}/audit-log.csv": OWNER,
    "GET /api/v1/orgs/{org_id}/audit-log/integrity": OWNER,
    "GET /api/v1/orgs/{org_id}/budgets": MEMBER,
    "PUT /api/v1/orgs/{org_id}/budgets": ADMIN,
    # Assistants and their versions.
    "GET /api/v1/assistants": MEMBER,
    "POST /api/v1/assistants": MEMBER,
    "POST /api/v1/assistants:from-sample": MEMBER,
    "POST /api/v1/pipeline:recommend": MEMBER,
    "GET /api/v1/assistants/{assistant_id}": MEMBER,
    "PATCH /api/v1/assistants/{assistant_id}": EDITOR,
    "DELETE /api/v1/assistants/{assistant_id}": EDITOR,
    "GET /api/v1/assistants/{assistant_id}/draft-graph": MEMBER,
    "PUT /api/v1/assistants/{assistant_id}/draft-graph": EDITOR,
    "PUT /api/v1/assistants/{assistant_id}/draft-config": EDITOR,
    "POST /api/v1/assistants/{assistant_id}/graph:validate": MEMBER,
    "POST /api/v1/assistants/{assistant_id}/graph:compile": MEMBER,
    "POST /api/v1/assistants/{assistant_id}/versions": EDITOR,
    # Reviewing a version, rolling back and pausing are an admin's (7b.1);
    # the builder may only take back their own submission.
    "POST /api/v1/assistants/{assistant_id}/versions/{number}/approve": ADMIN,
    "POST /api/v1/assistants/{assistant_id}/versions/{number}/reject": ADMIN,
    "POST /api/v1/assistants/{assistant_id}/versions/{number}/make-live": ADMIN,
    "POST /api/v1/assistants/{assistant_id}/versions/{number}/withdraw": EDITOR,
    "POST /api/v1/assistants/{assistant_id}/archive": ADMIN,
    "POST /api/v1/assistants/{assistant_id}/unarchive": ADMIN,
    "GET /api/v1/assistants/{assistant_id}/versions": MEMBER,
    # Co-editors: anyone sees them; the creator or an admin names them.
    "GET /api/v1/assistants/{assistant_id}/editors": MEMBER,
    "POST /api/v1/assistants/{assistant_id}/editors": EDITOR,
    "DELETE /api/v1/assistants/{assistant_id}/editors/{user_id}": EDITOR,
    "GET /api/v1/assistants/{assistant_id}/versions/diff": MEMBER,
    "GET /api/v1/assistants/{assistant_id}/versions/{number}": MEMBER,
    "GET /api/v1/assistants/{assistant_id}/versions/{number}/drift": MEMBER,
    "POST /api/v1/assistants/{assistant_id}/prompt:generate": EDITOR,
    "POST /api/v1/assistants/{assistant_id}/pipeline:recommend": EDITOR,
    "GET /api/v1/assistants/{assistant_id}/budget": MEMBER,
    "PUT /api/v1/assistants/{assistant_id}/budget": ADMIN,
    "GET /api/v1/assistants/{assistant_id}/memories": CHAT,
    "DELETE /api/v1/assistants/{assistant_id}/memories": CHAT,
    # Conversations.
    "POST /api/v1/assistants/{assistant_id}/conversations": CHAT,
    "GET /api/v1/assistants/{assistant_id}/conversations": CHAT,
    "GET /api/v1/conversations/{conversation_id}": CHAT_READER,
    "PATCH /api/v1/conversations/{conversation_id}": CHAT_STARTER,
    "DELETE /api/v1/conversations/{conversation_id}": CHAT_STARTER,
    # Read-only links (Phase 9.4): the starter shares; opening is checked
    # inside (a member allowed the assistant and what it cites).
    "POST /api/v1/conversations/{conversation_id}/shares": CHAT_STARTER,
    "GET /api/v1/conversations/{conversation_id}/shares": CHAT_READER,
    "DELETE /api/v1/conversations/{conversation_id}/shares/{share_id}": CHAT_STARTER,
    "GET /api/v1/shared/{token}": SIGNED_IN,
    # Hand-off to a person (Phase 9.5) and screenshots (9.6).
    "POST /api/v1/conversations/{conversation_id}/handoff": CHAT_STARTER,
    "GET /api/v1/conversations/{conversation_id}/handoffs": CHAT_READER,
    "POST /api/v1/conversations/{conversation_id}/attachments": CHAT_STARTER,
    "GET /api/v1/conversations/{conversation_id}/attachments/{attachment_id}/content": CHAT_READER,
    # Insight (Phase 10): analytics, canvas traffic, approval evidence.
    "GET /api/v1/assistants/{assistant_id}/analytics": EDITOR,
    "GET /api/v1/assistants/{assistant_id}/traffic": MEMBER,
    "GET /api/v1/orgs/{org_id}/analytics": ADMIN,
    "GET /api/v1/orgs/{org_id}/analytics.csv": ADMIN,
    "GET /api/v1/assistants/{assistant_id}/versions/{number}/evidence": ADMIN,
    # The needs-review queue (10.3) and conversation export (10.5).
    "GET /api/v1/assistants/{assistant_id}/review-queue": EDITOR,
    "POST /api/v1/assistants/{assistant_id}/review-queue/{item_id}:to-case": EDITOR,
    "POST /api/v1/assistants/{assistant_id}/review-queue/{item_id}:dismiss": EDITOR,
    "GET /api/v1/conversations/{conversation_id}/export": CHAT_READER,
    "POST /api/v1/conversations/{conversation_id}:interrupt": CHAT_STARTER,
    "POST /api/v1/conversations/{conversation_id}:restore": CHAT_STARTER,
    "PUT /api/v1/conversations/{conversation_id}/messages/{message_id}/feedback": CHAT_STARTER,
    "PUT /api/v1/conversations/{conversation_id}/outcome": CHAT_STARTER,
    "GET /api/v1/conversations/{conversation_id}/messages": CHAT_READER,
    "POST /api/v1/conversations/{conversation_id}/messages": CHAT_STARTER,
    "GET /api/v1/conversations/{conversation_id}/runs": READER,
    "GET /api/v1/conversations/{conversation_id}/runs/{run_id}": READER,
    "GET /api/v1/conversations/{conversation_id}/turn": CHAT_READER,
    "GET /api/v1/conversations/{conversation_id}/turns/{turn_id}/events": CHAT_READER,
    "GET /api/v1/conversations/{conversation_id}/approvals": CHAT_READER,
    "POST /api/v1/approvals/{approval_id}:resolve": DECIDER,
    # Phase 9.1: the asker withdraws their own; the queue of what you may
    # decide, and standing in while someone is away, are everyone's.
    "POST /api/v1/approvals/{approval_id}:withdraw": CHAT_STARTER,
    "GET /api/v1/approvals/decidable": CHAT,
    # Phase 9.2: the inbox's views, the counts, and one request's page.
    "GET /api/v1/approvals/summary": CHAT,
    "GET /api/v1/approvals/mine": CHAT,
    "GET /api/v1/approvals/decided": CHAT,
    "GET /api/v1/approvals/{approval_id}": REQUEST,
    "GET /api/v1/orgs/{org_id}/approvals": ADMIN,
    "GET /api/v1/approvals/delegations": CHAT,
    "POST /api/v1/approvals/delegations": CHAT,
    "DELETE /api/v1/approvals/delegations/{delegation_id}": SIGNED_IN,
    "GET /api/v1/assistants/{assistant_id}/approvers": EDITOR,
    "PUT /api/v1/assistants/{assistant_id}/approvers": ADMIN,
    "POST /api/v1/assistants/{assistant_id}/actions:check": EDITOR,
    # Knowledge.
    "GET /api/v1/assistants/{assistant_id}/data-sources": MEMBER,
    "POST /api/v1/assistants/{assistant_id}/data-sources": EDITOR,
    "POST /api/v1/assistants/{assistant_id}/data-sources/upload": EDITOR,
    "GET /api/v1/assistants/{assistant_id}/data-sources/{data_source_id}": MEMBER,
    "GET /api/v1/assistants/{assistant_id}/data-sources/{data_source_id}/content-url": CHAT,
    "POST /api/v1/assistants/{assistant_id}/data-sources/{data_source_id}:reindex": EDITOR,
    "DELETE /api/v1/assistants/{assistant_id}/data-sources/{data_source_id}": EDITOR,
    # Who may read it, and the scan's confirmation (Phase 9.4).
    "PUT /api/v1/assistants/{assistant_id}/data-sources/{data_source_id}/access": EDITOR,
    "POST /api/v1/assistants/{assistant_id}/data-sources/{data_source_id}:confirm-everyone": (
        EDITOR
    ),
    # Databases.
    "GET /api/v1/assistants/{assistant_id}/db-connections": MEMBER,
    "POST /api/v1/assistants/{assistant_id}/db-connections": EDITOR,
    "GET /api/v1/assistants/{assistant_id}/db-connections/{connection_id}": MEMBER,
    "PATCH /api/v1/assistants/{assistant_id}/db-connections/{connection_id}": EDITOR,
    "DELETE /api/v1/assistants/{assistant_id}/db-connections/{connection_id}": EDITOR,
    "POST /api/v1/assistants/{assistant_id}/db-connections/{connection_id}:test": EDITOR,
    "POST /api/v1/assistants/{assistant_id}/db-connections/{connection_id}:refresh-schema": EDITOR,
    "GET /api/v1/assistants/{assistant_id}/db-connections/{connection_id}/schema": MEMBER,
    # MCP servers.
    "GET /api/v1/assistants/{assistant_id}/mcp-servers": MEMBER,
    "POST /api/v1/assistants/{assistant_id}/mcp-servers": EDITOR,
    "GET /api/v1/assistants/{assistant_id}/mcp-servers/{server_id}": MEMBER,
    "PATCH /api/v1/assistants/{assistant_id}/mcp-servers/{server_id}": EDITOR,
    "DELETE /api/v1/assistants/{assistant_id}/mcp-servers/{server_id}": EDITOR,
    "POST /api/v1/assistants/{assistant_id}/mcp-servers/{server_id}:health": EDITOR,
    "POST /api/v1/assistants/{assistant_id}/mcp-servers/{server_id}:discover-tools": EDITOR,
    # HTTP connections (Phase 9.3): the builder's, headers write-only.
    "GET /api/v1/assistants/{assistant_id}/http-connections": EDITOR,
    "POST /api/v1/assistants/{assistant_id}/http-connections": EDITOR,
    "PATCH /api/v1/assistants/{assistant_id}/http-connections/{http_connection_id}": EDITOR,
    "DELETE /api/v1/assistants/{assistant_id}/http-connections/{http_connection_id}": EDITOR,
    "GET /api/v1/mcp-presets": MEMBER,
    "GET /api/v1/mcp-runner": MEMBER,
    # Evals.
    "GET /api/v1/assistants/{assistant_id}/eval-suites": MEMBER,
    "POST /api/v1/assistants/{assistant_id}/eval-suites": EDITOR,
    "GET /api/v1/eval-suites/{suite_id}": EDITOR,  # Phase 9.9: answers and questions inside
    "PATCH /api/v1/eval-suites/{suite_id}": EDITOR,
    "DELETE /api/v1/eval-suites/{suite_id}": EDITOR,
    "POST /api/v1/eval-suites/{suite_id}/cases:bulk": EDITOR,
    "PUT /api/v1/eval-suites/{suite_id}/cases/{case_id}": EDITOR,
    "DELETE /api/v1/eval-suites/{suite_id}/cases/{case_id}": EDITOR,
    "POST /api/v1/eval-suites/{suite_id}/runs": EDITOR,
    "GET /api/v1/eval-suites/{suite_id}/runs": EDITOR,
    "GET /api/v1/eval-runs/{run_id}": EDITOR,
    "POST /api/v1/eval-runs/{run_id}:cancel": EDITOR,
}

#: Who passes each level, among the walk's five people. The employee only
#: ever passes a Chat level (Phase 8.1).
PASSES: dict[str, set[str]] = {
    CHAT: {"owner", "admin", "creator", "member", "employee"},
    CHAT_READER: {"owner", "admin", "creator"},
    CHAT_STARTER: {"creator"},
    MEMBER: {"owner", "admin", "creator", "member"},
    EDITOR: {"owner", "admin", "creator"},
    STARTER: {"creator"},
    DECIDER: {"owner", "admin"},
    REQUEST: {"owner", "admin", "creator"},
    ADMIN: {"owner", "admin"},
    READER: {"owner", "admin", "creator"},
    OWNER: {"owner"},
}

#: Routes that check the caller's role in the handler, after the body is
#: read: a refusal needs a valid body to be reached.
BODIES: dict[str, Any] = {
    "POST /api/v1/approvals/{approval_id}:resolve": {"decision": "approved"},
    "PUT /api/v1/assistants/{assistant_id}/budget": {"daily_usd": 5},
}


def _routes() -> list[tuple[str, str, bool]]:
    """(key, path, takes a body) for every route the API publishes."""
    out = []
    for path, operations in sorted(app.openapi()["paths"].items()):
        for method in METHODS:
            if method in operations:
                out.append((f"{method.upper()} {path}", path, "requestBody" in operations[method]))
    return out


def test_every_route_is_in_the_matrix_and_nothing_else_is() -> None:
    published = {key for key, _, _ in _routes()}
    missing = sorted(published - ROLE_MATRIX.keys())
    stale = sorted(ROLE_MATRIX.keys() - published)
    assert missing == [], f"decide who may call these: {missing}"
    assert stale == [], f"no longer published: {stale}"
    assert len(published) > 80, "the walk should cover the whole API"


def test_the_threat_model_lists_who_may_call_each_route() -> None:
    """THREAT_MODEL.md's appendix is this matrix in words (Phase 7b.10)."""
    from tests.role_table import current, render

    assert current() == render(ROLE_MATRIX), (
        "the threat model's route list is out of date: run `python -m tests.role_table`"
    )


def test_the_studio_floor_leaves_out_the_employee_and_nobody_else() -> None:
    """The floor was built for the Employee role (7a.3); since 8.1 it is the
    only role below it."""
    assert access.STUDIO_FLOOR is MemberRole.member
    below = {role for role in MemberRole if not role.satisfies(access.STUDIO_FLOOR)}
    assert below == {MemberRole.employee}


# ── the org the walk asks about ──────────────────────────────


@dataclass
class Team:
    people: dict[str, Actor]
    org_id: str
    ids: dict[str, str]
    eval_run_id: str

    def h(self, who: str) -> dict[str, str]:
        # A reason with every call: an admin opening someone's conversation
        # must give one (7b.2); everywhere else it is ignored.
        return {**self.people[who].headers, "X-Org-Id": self.org_id, "X-Read-Reason": "walk"}


async def _team(client: AsyncClient, tmp: Path) -> Team:
    owner = await _register(client, "owner@example.com")
    org = (await client.post(f"{API}/orgs", json={"name": "Team"}, headers=owner.headers)).json()
    people = {"owner": owner}
    for name, role in (
        ("admin", "admin"),
        ("creator", "member"),
        ("member", "member"),
        ("employee", "employee"),
    ):
        actor = await _register(client, f"{name}@example.com")
        inv = await client.post(
            f"{API}/orgs/{org['id']}/invites",
            json={"email": f"{name}@example.com", "role": role},
            headers=owner.headers,
        )
        assert inv.status_code == 201, inv.text
        token = inv.json()["accept_url"].rsplit("/", 1)[-1]
        accepted = await client.post(f"{API}/invites/{token}/accept", headers=actor.headers)
        assert accepted.is_success, accepted.text
        people[name] = actor

    # A group, for the group routes (Phase 8.5).
    group = await client.post(
        f"{API}/orgs/{org['id']}/groups", json={"name": "IT"}, headers=owner.headers
    )
    assert group.status_code == 201, group.text

    # An invite nobody has used, for the invite routes.
    open_invite = await client.post(
        f"{API}/orgs/{org['id']}/invites",
        json={"email": "later@example.com", "role": "member"},
        headers=owner.headers,
    )
    assert open_invite.status_code == 201, open_invite.text
    h = {**people["creator"].headers, "X-Org-Id": org["id"]}

    async def made(method: str, url: str, **kw: Any) -> dict[str, Any]:
        r = await client.request(method, f"{API}{url}", headers=h, **kw)
        assert r.status_code in (200, 201, 202), f"{method} {url}: {r.status_code} {r.text}"
        return dict(r.json())

    # Everything belongs to the creator, a plain member.
    aid = (await made("POST", "/assistants", json={"name": "Helpdesk"}))["id"]
    # Submitted by its creator, approved by an admin: Chat answers only
    # from an approved version (Phase 7b.1).
    await go_live(client, aid, h, "v1", approver={**people["admin"].headers, "X-Org-Id": org["id"]})
    conv = await made("POST", f"/assistants/{aid}/conversations", json={})
    async with client.stream(
        "POST", f"{API}/conversations/{conv['id']}/messages", json={"text": "hello"}, headers=h
    ) as stream:
        turn_id = stream.headers["x-turn-id"]
        async for _ in stream.aiter_lines():
            pass
    run = (await made("GET", f"/conversations/{conv['id']}/runs"))["items"][0]
    source = await made(
        "POST",
        f"/assistants/{aid}/data-sources",
        json={"type": "text", "name": "Notes", "text": "Password resets take a day."},
    )
    db = tmp / f"{uuid.uuid4().hex}.db"
    sqlite3.connect(db).close()
    conn = await made(
        "POST",
        f"/assistants/{aid}/db-connections",
        json={"name": "Tickets", "engine": "sqlite", "database": str(db)},
    )
    suite = await made("POST", f"/assistants/{aid}/eval-suites", json={"name": "Basics"})
    detail = await made(
        "POST", f"/eval-suites/{suite['id']}/cases:bulk", json={"cases": [{"input": "hello"}]}
    )
    eval_run = await made("POST", f"/eval-suites/{suite['id']}/runs", json={})
    await runner.run(uuid.UUID(eval_run["id"]))

    async with get_sessionmaker()() as s:
        server = McpServer(
            assistant_id=uuid.UUID(aid),
            org_id=uuid.UUID(org["id"]),
            name="files",
            transport=McpTransport.http,
            url="https://mcp.example.com/mcp",
        )
        s.add(server)
        http_conn = HttpConnection(
            assistant_id=uuid.UUID(aid),
            org_id=uuid.UUID(org["id"]),
            name="Tickets",
            base_url="https://tickets.example.com/api",
            header_names=[],
        )
        s.add(http_conn)
        share = ConversationShare(
            org_id=uuid.UUID(org["id"]),
            conversation_id=uuid.UUID(conv["id"]),
            created_by=uuid.UUID(owner.user_id),
            token_hash=uuid.uuid4().hex,
        )
        s.add(share)
        attachment = Attachment(
            org_id=uuid.UUID(org["id"]),
            conversation_id=uuid.UUID(conv["id"]),
            uploaded_by=uuid.UUID(owner.user_id),
            object_key="attachments/test.png",
            content_type="image/png",
            bytes=8,
        )
        s.add(attachment)
        await s.commit()
        approval = await waiting_request(
            s,
            conversation_id=uuid.UUID(conv["id"]),
            tool_name="mcp__caps__sql_query",
            tool_input={"sql": "UPDATE tickets SET status = 'closed'"},
            risk="high",
            rationale="writes",
        )
        server_id, approval_id = str(server.id), str(approval.id)
        http_connection_id = str(http_conn.id)
        share_id = str(share.id)
        attachment_id = str(attachment.id)

    # The owner's stand-in while away (Phase 9.1), for its route.
    handed = await client.post(
        f"{API}/approvals/delegations",
        json={
            "email": "admin@example.com",
            "until": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
        },
        headers={**owner.headers, "X-Org-Id": org["id"]},
    )
    assert handed.status_code == 201, handed.text

    return Team(
        people=people,
        org_id=org["id"],
        eval_run_id=eval_run["id"],
        ids={
            "org_id": org["id"],
            "assistant_id": aid,
            "number": "1",
            "conversation_id": conv["id"],
            "run_id": run["id"],
            # The run's answer, for its feedback (Phase 8.8).
            "message_id": run["message_id"],
            "turn_id": turn_id,
            "data_source_id": source["id"],
            "connection_id": conn["id"],
            "server_id": server_id,
            "http_connection_id": http_connection_id,
            "share_id": share_id,
            "attachment_id": attachment_id,
            # A review item is named by its answer's message id (10.3).
            "item_id": run["message_id"],
            "approval_id": approval_id,
            "delegation_id": handed.json()[0]["id"],
            "invite_id": open_invite.json()["id"],
            "group_id": group.json()["id"],
            "suite_id": suite["id"],
            "case_id": detail["cases"][0]["id"],
            # A role change aimed at the plain member.
            "user_id": people["member"].user_id,
        },
    )


def _fill(path: str, team: Team) -> str:
    url = path
    for name, value in team.ids.items():
        filled = team.eval_run_id if name == "run_id" and "/eval-runs/" in path else value
        url = url.replace("{" + name + "}", filled)
    assert "{" not in url, f"no id for {url}: add it to _team"
    return url


@pytest.fixture
async def team(client: AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Team:
    async def enqueue(_id: uuid.UUID) -> bool:
        return True

    monkeypatch.setattr(queue, "enqueue_eval", enqueue)

    async def stored(_key: str) -> bytes:
        return b"\x89PNG\r\n\x1a\n"

    # A screenshot's bytes, without object storage (Phase 9.6).
    monkeypatch.setattr("app.services.attachments.get_object", stored)
    monkeypatch.setattr(queue, "enqueue_ingest", enqueue)
    return await _team(client, tmp_path)


def _org_routes() -> list[tuple[str, str, bool]]:
    return [r for r in _routes() if ROLE_MATRIX[r[0]] not in (PUBLIC, SIGNED_IN)]


# ── the walk ─────────────────────────────────────────────────


async def test_each_role_is_refused_exactly_where_the_matrix_says(
    client: AsyncClient, team: Team
) -> None:
    wrong: list[str] = []
    refused = 0
    for key, path, _ in _org_routes():
        method = key.split(" ", 1)[0]
        for who in ("employee", "member", "creator", "admin", "owner"):
            if who in PASSES[ROLE_MATRIX[key]]:
                continue
            r = await client.request(
                method, _fill(path, team), headers=team.h(who), json=BODIES.get(key, {})
            )
            refused += 1
            if r.status_code != 403:
                wrong.append(f"{who}: {key} -> {r.status_code} {r.text[:120]}")
    assert wrong == [], "\n".join(wrong)
    assert refused > 40


async def test_each_role_gets_through_where_the_matrix_says(
    client: AsyncClient, team: Team
) -> None:
    """Reads are sent as they are; writes with a body are sent a body of the
    wrong shape, so passing the checks shows up as 422 and changes nothing."""
    wrong: list[str] = []
    tried = 0
    for key, path, has_body in _org_routes():
        method = key.split(" ", 1)[0]
        if method != "GET" and not has_body:
            continue
        for who in sorted(PASSES[ROLE_MATRIX[key]]):
            kw: dict[str, Any] = {} if method == "GET" else {"json": []}
            r = await client.request(method, _fill(path, team), headers=team.h(who), **kw)
            tried += 1
            if r.status_code in (401, 403, 404) or (method != "GET" and r.status_code != 422):
                wrong.append(f"{who}: {key} -> {r.status_code} {r.text[:120]}")
    assert wrong == [], "\n".join(wrong)
    assert tried > 150


async def test_an_employee_reaches_chat_and_nothing_else(client: AsyncClient, team: Team) -> None:
    """Deny by default (Phase 7a.3), now with the role it was built for
    (8.1): outside the Chat levels every org route refuses an employee at
    the Studio floor; inside them, any refusal is about whose conversation
    it is, never about the role. Deciding a request is an approver's."""
    chat_levels = {CHAT, CHAT_READER, CHAT_STARTER, REQUEST}
    wrong: list[str] = []
    for key, path, _ in _org_routes():
        method = key.split(" ", 1)[0]
        r = await client.request(
            method, _fill(path, team), headers=team.h("employee"), json=BODIES.get(key, {})
        )
        code = r.json().get("error", {}).get("code") if r.status_code == 403 else None
        if key == "POST /api/v1/approvals/{approval_id}:resolve":
            expected_ok = code == "not_an_approver"
        elif ROLE_MATRIX[key] in chat_levels:
            expected_ok = code != "studio_access_required"
        else:
            expected_ok = code == "studio_access_required"
        if not expected_ok:
            wrong.append(f"{key} -> {r.status_code} {r.text[:120]}")
    assert wrong == [], "\n".join(wrong)

    # Their own profile and org list still answer: they are signed in.
    for url in ("/auth/me", "/orgs"):
        assert (await client.get(f"{API}{url}", headers=team.h("employee"))).status_code == 200


async def test_nothing_changed_after_the_walk(client: AsyncClient, team: Team) -> None:
    """Every refusal above came before anything was written."""
    for key, path, _ in _org_routes():
        method = key.split(" ", 1)[0]
        if method == "GET":
            continue
        for who in ("employee", "member", "creator", "admin"):
            if who not in PASSES[ROLE_MATRIX[key]]:
                await client.request(
                    method, _fill(path, team), headers=team.h(who), json=BODIES.get(key, {})
                )
    ids, h = team.ids, team.h("owner")
    for url in (
        f"/assistants/{ids['assistant_id']}",
        f"/conversations/{ids['conversation_id']}",
        f"/assistants/{ids['assistant_id']}/data-sources/{ids['data_source_id']}",
        f"/assistants/{ids['assistant_id']}/db-connections/{ids['connection_id']}",
        f"/assistants/{ids['assistant_id']}/mcp-servers/{ids['server_id']}",
        f"/eval-suites/{ids['suite_id']}",
    ):
        assert (await client.get(f"{API}{url}", headers=h)).status_code == 200, url
    http = await client.get(f"{API}/assistants/{ids['assistant_id']}/http-connections", headers=h)
    assert [c["id"] for c in http.json()] == [ids["http_connection_id"]]
    pending = await client.get(f"{API}/conversations/{ids['conversation_id']}/approvals", headers=h)
    assert [a["id"] for a in pending.json()["items"]] == [ids["approval_id"]], "still undecided"
    roles = (await client.get(f"{API}/orgs/{team.org_id}/members", headers=h)).json()["items"]
    assert {r["user_id"]: r["role"] for r in roles}[ids["user_id"]] == "member"
    budget = (await client.get(f"{API}/assistants/{ids['assistant_id']}/budget", headers=h)).json()
    assert budget["budgets"] == [], "no refused caller set a budget"
