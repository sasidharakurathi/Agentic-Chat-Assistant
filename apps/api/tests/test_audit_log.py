"""The audit log (Phase 7b.7): complete, tamper-evident, readable.

- **Complete.** Every route that changes something is in `AUDITED`, with
  the action it records or the reason it records none; a route added
  without an entry fails, so nobody has to remember. Each action named is
  one the code records.
- **Tamper-evident.** Sealed rows form a hash chain; changing, removing or
  reordering one is caught, and where.
- **Readable.** Admins filter it; the owner exports it (itself recorded)
  and checks the chain.
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path
from typing import Any

import pytest
from app.db.session import get_sessionmaker
from app.main import app
from app.models.audit_log import AuditLog
from app.services import audit, audit_chain
from httpx import AsyncClient
from sqlalchemy import select, text, update

from test_rbac_escalation import Team, _team  # type: ignore[import-not-found]

pytestmark = pytest.mark.anyio

API = "/api/v1"
APP_DIR = Path(__file__).resolve().parents[1] / "app"

#: Nothing to record: the route reads, tests, or acts only on the caller's
#: own work, which other records already cover.
EXEMPT = "exempt"

AUDITED: dict[str, str] = {
    # Sign-in and accounts.
    "POST /api/v1/auth/register": "user.register",
    "POST /api/v1/auth/login": "user.login",
    "POST /api/v1/auth/logout": "user.logout",
    "POST /api/v1/auth/refresh": EXEMPT,  # each sign-in is recorded; renewals are routine
    "POST /api/v1/auth/password": "user.password_change",
    # Google sign-in (Phase 8.3): the callback records the sign-in, the
    # registration or the link; the ticket and the code's exchange change
    # nothing of their own.
    "POST /api/v1/auth/google/link-ticket": EXEMPT,
    # Two-step sign-in (Phase 8.4). A setup changes nothing until confirmed.
    "POST /api/v1/auth/login/two-factor": "user.login",
    "POST /api/v1/auth/device-code": "user.device_code.create",
    "POST /api/v1/auth/device-code/redeem": "user.login",
    "POST /api/v1/auth/two-factor/setup": EXEMPT,
    "POST /api/v1/auth/two-factor/confirm": "user.two_factor.enable",
    "POST /api/v1/auth/two-factor/verify": "user.two_factor.verify",
    "POST /api/v1/auth/two-factor/recovery-codes": "user.two_factor.recovery_codes",
    "POST /api/v1/auth/two-factor/disable": "user.two_factor.disable",
    "POST /api/v1/auth/google/exchange": EXEMPT,
    "DELETE /api/v1/auth/google": "user.identity.unlink",
    "POST /api/v1/auth/password-reset": "user.password_reset_requested",
    "POST /api/v1/auth/password-reset/confirm": "user.password_reset",
    "POST /api/v1/auth/verify-email": "user.email_verified",
    "POST /api/v1/auth/verify-email/send": EXEMPT,  # sends a link; using it is recorded
    # The org, people and invites.
    "POST /api/v1/orgs": "org.create",
    "PATCH /api/v1/orgs/{org_id}/members/{user_id}": "org.member.role_change",
    "POST /api/v1/orgs/{org_id}/members/{user_id}:deactivate": "org.member.deactivate",
    "POST /api/v1/orgs/{org_id}/members/{user_id}:reactivate": "org.member.reactivate",
    "POST /api/v1/orgs/{org_id}/invites": "org.invite.create",
    "DELETE /api/v1/orgs/{org_id}/invites/{invite_id}": "org.invite.revoke",
    "POST /api/v1/invites/{token}/accept": "org.invite.accept",
    "PUT /api/v1/orgs/{org_id}/budgets": "budget.updated",
    "POST /api/v1/orgs/{org_id}/mail:test": "mail.test",
    "PATCH /api/v1/orgs/{org_id}/settings": "org.settings.update",
    "PUT /api/v1/orgs/{org_id}/budgets/people": "budget.updated",
    "PUT /api/v1/orgs/{org_id}/members/{user_id}/budget": "budget.updated",
    "POST /api/v1/orgs/{org_id}/alerts:resolve": "alert.resolve",
    "PUT /api/v1/orgs/{org_id}/retention": "org.retention.update",
    "POST /api/v1/orgs/{org_id}/members/{user_id}:erase": "person.erase",
    "POST /api/v1/orgs/{org_id}/holds": "legal_hold.create",
    "POST /api/v1/orgs/{org_id}/holds:release": "legal_hold.release",
    "PUT /api/v1/orgs/{org_id}/notice": "privacy_notice.update",
    "POST /api/v1/chat/notice:accept": "privacy_notice.accept",
    # Assistants, versions, review.
    "POST /api/v1/assistants": "assistant.create",
    "POST /api/v1/assistants:from-sample": "assistant.create",
    "PATCH /api/v1/assistants/{assistant_id}": "assistant.update",
    "DELETE /api/v1/assistants/{assistant_id}": "assistant.delete",
    "PUT /api/v1/assistants/{assistant_id}/draft-graph": EXEMPT,  # drafts; versions are recorded
    "PUT /api/v1/assistants/{assistant_id}/draft-config": EXEMPT,  # drafts; versions are recorded
    "PUT /api/v1/assistants/{assistant_id}/listing": EXEMPT,  # a draft, frozen into the version
    "POST /api/v1/assistants/{assistant_id}:official": "assistant.official",
    "POST /api/v1/orgs/{org_id}/groups": "group.create",
    "PATCH /api/v1/orgs/{org_id}/groups/{group_id}": "group.update",
    "DELETE /api/v1/orgs/{org_id}/groups/{group_id}": "group.delete",
    "POST /api/v1/orgs/{org_id}/groups/{group_id}/members": "group.member.add",
    "DELETE /api/v1/orgs/{org_id}/groups/{group_id}/members/{user_id}": "group.member.remove",
    "POST /api/v1/assistants/{assistant_id}/graph:validate": EXEMPT,  # changes nothing
    "POST /api/v1/assistants/{assistant_id}/graph:compile": EXEMPT,  # changes nothing
    "POST /api/v1/assistants/{assistant_id}/prompt:generate": EXEMPT,  # a suggestion only
    "POST /api/v1/assistants/{assistant_id}/pipeline:recommend": EXEMPT,  # a suggestion only
    "POST /api/v1/pipeline:recommend": EXEMPT,  # a suggestion only
    "POST /api/v1/assistants/{assistant_id}/versions": "assistant.version.submit",
    "POST /api/v1/assistants/{assistant_id}/versions/{number}/approve": "assistant.version.approve",
    "POST /api/v1/assistants/{assistant_id}/versions/{number}/reject": "assistant.version.reject",
    "POST /api/v1/assistants/{assistant_id}/versions/{number}/withdraw": (
        "assistant.version.withdraw"
    ),
    "POST /api/v1/assistants/{assistant_id}/versions/{number}/make-live": (
        "assistant.version.rollback"
    ),
    "POST /api/v1/assistants/{assistant_id}/archive": "assistant.archive",
    "POST /api/v1/assistants/{assistant_id}/unarchive": "assistant.unarchive",
    "POST /api/v1/assistants/{assistant_id}/editors": "assistant.editor.add",
    "DELETE /api/v1/assistants/{assistant_id}/editors/{user_id}": "assistant.editor.remove",
    "PUT /api/v1/assistants/{assistant_id}/budget": "budget.updated",
    "DELETE /api/v1/assistants/{assistant_id}/memories": "memory.clear",
    # What an assistant reaches.
    "POST /api/v1/assistants/{assistant_id}/data-sources": "data_source.create",
    "POST /api/v1/assistants/{assistant_id}/data-sources/upload": "data_source.create",
    "DELETE /api/v1/assistants/{assistant_id}/data-sources/{data_source_id}": (
        "data_source.delete"
    ),
    "POST /api/v1/assistants/{assistant_id}/data-sources/{data_source_id}:reindex": (
        "data_source.reindex"
    ),
    "POST /api/v1/assistants/{assistant_id}/db-connections": "db_connection.create",
    "PATCH /api/v1/assistants/{assistant_id}/db-connections/{connection_id}": (
        "db_connection.update"
    ),
    "DELETE /api/v1/assistants/{assistant_id}/db-connections/{connection_id}": (
        "db_connection.delete"
    ),
    "POST /api/v1/assistants/{assistant_id}/db-connections/{connection_id}:test": EXEMPT,
    "POST /api/v1/assistants/{assistant_id}/db-connections/{connection_id}:refresh-schema": EXEMPT,
    "POST /api/v1/assistants/{assistant_id}/mcp-servers": "mcp_server.create",
    "PATCH /api/v1/assistants/{assistant_id}/mcp-servers/{server_id}": "mcp_server.update",
    "DELETE /api/v1/assistants/{assistant_id}/mcp-servers/{server_id}": "mcp_server.delete",
    "POST /api/v1/assistants/{assistant_id}/mcp-servers/{server_id}:discover-tools": (
        "mcp_server.discover"
    ),
    "POST /api/v1/assistants/{assistant_id}/mcp-servers/{server_id}:health": EXEMPT,
    "POST /api/v1/assistants/{assistant_id}/http-connections": "http_connection.create",
    "PATCH /api/v1/assistants/{assistant_id}/http-connections/{http_connection_id}": (
        "http_connection.update"
    ),
    "DELETE /api/v1/assistants/{assistant_id}/http-connections/{http_connection_id}": (
        "http_connection.delete"
    ),
    # Conversations: the person's own; runs and messages are their record.
    "POST /api/v1/assistants/{assistant_id}/conversations": EXEMPT,
    "PATCH /api/v1/conversations/{conversation_id}": "conversation.rename",
    "POST /api/v1/conversations/{conversation_id}/shares": "conversation.share_create",
    "POST /api/v1/conversations/{conversation_id}/handoff": "conversation.handoff",
    "POST /api/v1/assistants/{assistant_id}/review-queue/{item_id}:to-case": (
        "review_item.to_case"
    ),
    "POST /api/v1/assistants/{assistant_id}/review-queue/{item_id}:dismiss": (
        "review_item.dismiss"
    ),
    # A screenshot is chat content, which the audit log never holds (9.6).
    "POST /api/v1/conversations/{conversation_id}/attachments": EXEMPT,
    "DELETE /api/v1/conversations/{conversation_id}/shares/{share_id}": (
        "conversation.share_revoke"
    ),
    "PUT /api/v1/assistants/{assistant_id}/data-sources/{data_source_id}/access": (
        "data_source.access"
    ),
    "POST /api/v1/assistants/{assistant_id}/data-sources/{data_source_id}:confirm-everyone": (
        "data_source.sensitive_confirmed"
    ),
    "DELETE /api/v1/conversations/{conversation_id}": "conversation.archive",
    "POST /api/v1/conversations/{conversation_id}:restore": "conversation.restore",
    # Phase 8.8: a person's view of an answer, not a security event.
    "PUT /api/v1/conversations/{conversation_id}/messages/{message_id}/feedback": EXEMPT,
    "PUT /api/v1/conversations/{conversation_id}/outcome": EXEMPT,
    "POST /api/v1/conversations/{conversation_id}/messages": EXEMPT,
    "POST /api/v1/conversations/{conversation_id}:interrupt": EXEMPT,
    "POST /api/v1/approvals/{approval_id}:resolve": "approval.",  # approval.approved / .denied
    # Phase 9.1: requests that wait, who decides them, and stand-ins.
    "POST /api/v1/approvals/{approval_id}:withdraw": "approval.withdrawn",
    "POST /api/v1/approvals/delegations": "approval.delegate",
    "DELETE /api/v1/approvals/delegations/{delegation_id}": "approval.delegate_end",
    "PUT /api/v1/assistants/{assistant_id}/approvers": "assistant.approvers.update",
    "POST /api/v1/assistants/{assistant_id}/actions:check": EXEMPT,
    # Evals.
    "POST /api/v1/assistants/{assistant_id}/eval-suites": "eval_suite.create",
    "PATCH /api/v1/eval-suites/{suite_id}": "eval_suite.update",
    "DELETE /api/v1/eval-suites/{suite_id}": "eval_suite.delete",
    "POST /api/v1/eval-suites/{suite_id}/cases:bulk": "eval_suite.cases",
    "PUT /api/v1/eval-suites/{suite_id}/cases/{case_id}": "eval_suite.cases",
    "DELETE /api/v1/eval-suites/{suite_id}/cases/{case_id}": "eval_suite.cases",
    "POST /api/v1/eval-suites/{suite_id}/runs": "eval_run.start",
    "POST /api/v1/eval-runs/{run_id}:cancel": "eval_run.cancel",
}


def _mutating_routes() -> set[str]:
    out = set()
    for path, ops in app.openapi()["paths"].items():
        for method in ("post", "put", "patch", "delete"):
            if method in ops:
                out.add(f"{method.upper()} {path}")
    return out


def test_every_route_that_changes_something_has_an_audit_decision() -> None:
    routes = _mutating_routes()
    assert sorted(routes - AUDITED.keys()) == [], "decide what these record"
    assert sorted(AUDITED.keys() - routes) == [], "no longer published"


def test_every_action_named_is_one_the_code_records() -> None:
    source = "\n".join(p.read_text(encoding="utf-8") for p in APP_DIR.rglob("*.py"))
    # `action="..."`, or a name handed to a helper that records it.
    recorded = set(re.findall(r'"([a-z_]+\.[a-z_.{}]+)"', source))
    missing = [
        a
        for a in set(AUDITED.values()) - {EXEMPT}
        if not any(r == a or r.startswith(a) for r in recorded)
    ]
    assert missing == []


# ── the chain ────────────────────────────────────────────────


async def _write(n: int) -> None:
    async with get_sessionmaker()() as s:
        for i in range(n):
            await audit.record(s, action=f"test.event{i}", meta={"i": i})
        await s.commit()


async def _check() -> audit_chain.Integrity:
    async with get_sessionmaker()() as s:
        await audit_chain.seal(s)
        return await audit_chain.verify(s)


async def test_sealed_rows_form_an_intact_chain() -> None:
    await _write(5)
    found = await _check()
    assert (found.sealed, found.unsealed, found.intact, found.broken_at) == (5, 0, True, None)
    await _write(3)
    async with get_sessionmaker()() as s:
        assert (await audit_chain.verify(s)).unsealed == 3, "waiting, not broken"
    found = await _check()
    assert (found.sealed, found.intact) == (8, True)
    async with get_sessionmaker()() as s:
        seqs = list(await s.scalars(select(AuditLog.seq).order_by(AuditLog.seq)))
    assert seqs == list(range(1, 9))


@pytest.mark.parametrize(
    "tamper",
    [
        "UPDATE audit_log SET meta = '{\"i\": 99}' WHERE seq = 3",
        "UPDATE audit_log SET action = 'test.innocent' WHERE seq = 3",
        "UPDATE audit_log SET actor_label = 'someone@else.example' WHERE seq = 3",
        "DELETE FROM audit_log WHERE seq = 3",
    ],
)
async def test_changing_or_removing_a_sealed_row_is_caught(tamper: str) -> None:
    """On SQLite there is no trigger: this is someone with direct access to
    the database, which the chain exists for."""
    await _write(5)
    assert (await _check()).intact
    async with get_sessionmaker()() as s:
        await s.execute(text(tamper))
        await s.commit()
    found = await _check()
    assert (found.intact, found.broken_at) == (False, 4 if tamper.startswith("DELETE") else 3)


async def test_a_deleted_user_doesnt_break_the_chain() -> None:
    """Their id is cleared by the database; the email kept in the row still
    says who it was, and the hashes still hold."""
    await _write(2)
    assert (await _check()).intact
    async with get_sessionmaker()() as s:
        await s.execute(update(AuditLog).values(actor_user_id=None, org_id=None))
        await s.commit()
    assert (await _check()).intact


# ── the page's API ───────────────────────────────────────────


@pytest.fixture
async def team(client: AsyncClient) -> Team:
    return await _team(client)


async def _entries(client: AsyncClient, team: Team, **params: Any) -> list[dict[str, Any]]:
    r = await client.get(
        f"{API}/orgs/{team.org_id}/audit-log", params=params, headers=team.h(team.admin)
    )
    assert r.status_code == 200, r.text
    return list(r.json()["items"])


async def test_admins_filter_the_log_and_see_who_acted(client: AsyncClient, team: Team) -> None:
    await client.post(f"{API}/assistants", json={"name": "Desk"}, headers=team.h(team.member))
    created = await _entries(client, team, action="assistant.")
    assert [e["action"] for e in created] == ["assistant.create"]
    assert created[0]["actor_label"] == "member@example.com"
    by_owner = await _entries(client, team, actor=team.owner.user_id)
    assert by_owner and all(e["actor_user_id"] == team.owner.user_id for e in by_owner)
    assert await _entries(client, team, action="nothing.like.this") == []


async def test_sign_ins_and_failed_ones_are_recorded(client: AsyncClient, team: Team) -> None:
    await client.post(
        f"{API}/auth/login", json={"email": "member@example.com", "password": "wrong-one"}
    )
    signed_in = await client.post(
        f"{API}/auth/login", json={"email": "member@example.com", "password": "supersecret"}
    )
    actions = [e["action"] for e in await _entries(client, team, action="user.login")]
    assert sorted(actions) == ["user.login", "user.login_failed"]
    await client.post(
        f"{API}/auth/logout", json={"refresh_token": signed_in.json()["refresh_token"]}
    )
    assert [e["action"] for e in await _entries(client, team, action="user.logout")] == [
        "user.logout"
    ]


async def test_the_log_narrows_to_a_period(client: AsyncClient, team: Team) -> None:
    assert await _entries(client, team, since="2000-01-01T00:00:00Z")
    assert await _entries(client, team, since="2999-01-01T00:00:00Z") == []
    assert await _entries(client, team, until="2000-01-01T00:00:00Z") == []


async def test_the_owner_exports_it_and_the_export_is_recorded(
    client: AsyncClient, team: Team
) -> None:
    r = await client.get(f"{API}/orgs/{team.org_id}/audit-log.csv", headers=team.h(team.admin))
    assert r.status_code == 403, "the owner's"
    r = await client.get(f"{API}/orgs/{team.org_id}/audit-log.csv", headers=team.h(team.owner))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(r.text)))
    assert rows[0][:3] == ["when", "seq", "action"]
    assert any(row[2] == "org.invite.create" for row in rows[1:])
    assert [e["action"] for e in await _entries(client, team, action="audit.export")] == [
        "audit.export"
    ]


async def test_the_owner_checks_the_chain(client: AsyncClient, team: Team) -> None:
    r = await client.get(
        f"{API}/orgs/{team.org_id}/audit-log/integrity", headers=team.h(team.owner)
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["intact"], body["unsealed"], body["broken_at"]) == (True, 0, None)
    assert body["sealed"] > 0 and len(body["last_hash"]) == 64
    r = await client.get(
        f"{API}/orgs/{team.org_id}/audit-log/integrity", headers=team.h(team.admin)
    )
    assert r.status_code == 403


async def test_each_new_kind_of_record_is_written(client: AsyncClient, team: Team) -> None:
    """The actions added in 7b.7, each from its route."""
    admin = team.h(team.admin)
    a = (await client.post(f"{API}/assistants", json={"name": "A"}, headers=admin)).json()["id"]
    await client.delete(f"{API}/assistants/{a}/memories", headers=admin)
    suite = (
        await client.post(f"{API}/assistants/{a}/eval-suites", json={"name": "S"}, headers=admin)
    ).json()
    await client.patch(f"{API}/eval-suites/{suite['id']}", json={"name": "S2"}, headers=admin)
    await client.post(
        f"{API}/eval-suites/{suite['id']}/cases:bulk",
        json={"cases": [{"input": "hello"}]},
        headers=admin,
    )
    await client.post(f"{API}/orgs/{team.org_id}/mail:test", headers=admin)
    found = {e["action"] for e in await _entries(client, team)}
    assert {"memory.clear", "eval_suite.update", "eval_suite.cases", "mail.test"} <= found


def test_the_migration_lets_only_the_seal_through() -> None:
    """The trigger text allows setting the seal once and nothing else."""
    from importlib import import_module

    mig = import_module("app.db.migrations.versions.c7b7a2b3c4d5_audit_chain")
    assert "OLD.seq IS NOT NULL AND" in mig.FUNCTION
    assert "NEW.meta::text IS DISTINCT FROM OLD.meta::text" in mig.FUNCTION


async def _sealed(n: int) -> list[AuditLog]:
    await _write(n)
    assert (await _check()).intact
    async with get_sessionmaker()() as s:
        return list(await s.scalars(select(AuditLog).order_by(AuditLog.seq)))


async def _relink(rows: list[AuditLog], start: int) -> None:
    """Recompute the hash of each row from `start` on, as someone covering
    their tracks would, keeping each row's own `seq`."""
    async with get_sessionmaker()() as s:
        prev = rows[start - 1].row_hash if start > 0 else audit_chain.GENESIS
        for row in rows[start:]:
            fresh = await s.get(AuditLog, row.id)
            assert fresh is not None
            digest = audit_chain.row_hash(fresh, prev or "", fresh.seq)
            await s.execute(
                update(AuditLog)
                .where(AuditLog.id == fresh.id)
                .values(prev_hash=prev, row_hash=digest)
            )
            prev = digest
        await s.commit()


async def test_an_edit_whose_hash_is_rewritten_still_breaks_the_next_link() -> None:
    """Each hash covers the one before it: rewriting one row's hash and the
    next row's pointer to it doesn't make the chain whole."""
    rows = await _sealed(5)
    async with get_sessionmaker()() as s:
        await s.execute(update(AuditLog).where(AuditLog.seq == 3).values(meta={"i": 99}))
        await s.commit()
        edited = await s.get(AuditLog, rows[2].id)
        assert edited is not None
        digest = audit_chain.row_hash(edited, rows[1].row_hash or "", 3)
        await s.execute(update(AuditLog).where(AuditLog.seq == 3).values(row_hash=digest))
        await s.execute(update(AuditLog).where(AuditLog.seq == 4).values(prev_hash=digest))
        await s.commit()
    found = await _check()
    assert (found.intact, found.broken_at) == (False, 4)


async def test_a_removed_row_shows_as_a_gap_even_after_relinking() -> None:
    rows = await _sealed(5)
    async with get_sessionmaker()() as s:
        await s.execute(text("DELETE FROM audit_log WHERE seq = 3"))
        await s.commit()
    await _relink([r for r in rows if r.seq != 3], 2)
    found = await _check()
    assert (found.intact, found.broken_at) == (False, 4)
