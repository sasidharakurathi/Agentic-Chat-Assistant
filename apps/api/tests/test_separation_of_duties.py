"""Separation of duties (Phase 7b.3).

Nobody approves their own assistant: whoever submitted a version or built
the assistant needs another admin or the owner. With nobody else able to
review, the owner may approve their own, with a note, and the record says
so. Tool-call approvals stay with the person in the conversation until
Phase 9 (plan D17), but each decision records who asked, who decided,
their role and whether it was the same person.
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from typing import Any

import pytest
from app.db.session import get_sessionmaker
from app.models.audit_log import AuditLog
from app.models.user import User
from httpx import AsyncClient, Response
from sqlalchemy import select
from tests.pending import decide, waiting_request
from tests.test_migrations import _alembic
from tests.test_version_review_migration import _insert
from tests.versions import submit

from test_rbac_escalation import Team, _team  # type: ignore[import-not-found]

pytestmark = pytest.mark.anyio

API = "/api/v1"


@pytest.fixture
async def team(client: AsyncClient) -> Team:
    return await _team(client)


async def _assistant(client: AsyncClient, h: dict[str, str]) -> str:
    r = await client.post(f"{API}/assistants", json={"name": "Desk"}, headers=h)
    return str(r.json()["id"])


async def _approve(
    client: AsyncClient, h: dict[str, str], aid: str, n: int, note: str = ""
) -> Response:
    return await client.post(
        f"{API}/assistants/{aid}/versions/{n}/approve", json={"note": note}, headers=h
    )


def _code(r: Response) -> str:
    return str(r.json()["error"]["code"])


async def _approval_meta() -> dict[str, Any]:
    async with get_sessionmaker()() as s:
        meta = await s.scalar(
            select(AuditLog.meta).where(AuditLog.action == "assistant.version.approve")
        )
    assert meta is not None
    return dict(meta)


async def _deactivate(*people: Any) -> None:
    async with get_sessionmaker()() as s:
        for p in people:
            user = await s.get(User, uuid.UUID(p.user_id))
            assert user is not None
            user.is_active = False
        await s.commit()


# ── assistant versions ───────────────────────────────────────


async def test_an_admin_cannot_approve_what_they_submitted(client: AsyncClient, team: Team) -> None:
    admin = team.h(team.admin)
    aid = await _assistant(client, admin)
    n = await submit(client, aid, admin)
    r = await _approve(client, admin, aid, n, "Looks fine to me")
    assert (r.status_code, _code(r)) == (403, "cannot_approve_own")

    r = await _approve(client, team.h(team.owner), aid, n)
    assert r.status_code == 200, r.text
    meta = await _approval_meta()
    assert (meta["submitted_by"], meta["approver_role"], meta["self_approved"]) == (
        team.admin.user_id,
        "owner",
        False,
    )


async def test_the_builder_cannot_approve_a_version_someone_else_submitted(
    client: AsyncClient, team: Team
) -> None:
    """Their own assistant, even when another admin pressed Submit."""
    aid = await _assistant(client, team.h(team.admin))
    n = await submit(client, aid, team.h(team.admin2))
    r = await _approve(client, team.h(team.admin), aid, n)
    assert (r.status_code, _code(r)) == (403, "cannot_approve_own")
    r = await _approve(client, team.h(team.admin2), aid, n)
    assert (r.status_code, _code(r)) == (403, "cannot_approve_own"), "nor the submitter"
    assert (await _approve(client, team.h(team.owner), aid, n)).status_code == 200


async def test_the_owners_own_version_goes_to_an_admin(client: AsyncClient, team: Team) -> None:
    owner = team.h(team.owner)
    aid = await _assistant(client, owner)
    n = await submit(client, aid, owner)
    r = await _approve(client, owner, aid, n, "Mine, and ready")
    assert (r.status_code, _code(r)) == (403, "cannot_approve_own"), "an admin can review"
    assert (await _approve(client, team.h(team.admin), aid, n)).status_code == 200


async def test_with_nobody_else_the_owner_approves_their_own_with_a_note(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    """A one-person organisation: the owner is the only reviewer."""
    aid = await _assistant(client, org_headers)
    n = await submit(client, aid, org_headers)
    r = await _approve(client, org_headers, aid, n)
    assert (r.status_code, _code(r)) == (403, "self_approval_needs_note")
    r = await _approve(client, org_headers, aid, n, "Tested in preview; only reviewer")
    assert r.status_code == 200, r.text
    meta = await _approval_meta()
    assert (meta["self_approved"], meta["approver_role"], meta["note"]) == (
        True,
        "owner",
        "Tested in preview; only reviewer",
    )


async def test_a_deactivated_admin_is_nobody_to_ask(client: AsyncClient, team: Team) -> None:
    owner = team.h(team.owner)
    aid = await _assistant(client, owner)
    n = await submit(client, aid, owner)
    await _deactivate(team.admin, team.admin2)
    r = await _approve(client, owner, aid, n, "Admins are away for good")
    assert r.status_code == 200, r.text
    assert (await _approval_meta())["self_approved"] is True


async def test_an_admin_with_only_the_owner_left_still_needs_the_owner(
    client: AsyncClient, team: Team
) -> None:
    """Only the owner may approve their own work, and only when alone."""
    admin = team.h(team.admin)
    aid = await _assistant(client, admin)
    n = await submit(client, aid, admin)
    await _deactivate(team.admin2)
    r = await _approve(client, admin, aid, n, "Nobody else around")
    assert (r.status_code, _code(r)) == (403, "cannot_approve_own")


async def test_members_list_says_who_is_active(client: AsyncClient, team: Team) -> None:
    await _deactivate(team.admin2)
    items = (
        await client.get(f"{API}/orgs/{team.org_id}/members", headers=team.h(team.owner))
    ).json()["items"]
    assert {i["user_id"]: i["is_active"] for i in items}[team.admin2.user_id] is False


# ── tool calls: decided as today, recorded honestly ──────────


async def _pending(client: AsyncClient, team: Team, who: Any) -> tuple[str, str]:
    aid = await _assistant(client, team.h(team.owner))
    owner_n = await submit(client, aid, team.h(team.owner))
    await _approve(client, team.h(team.admin), aid, owner_n)
    c = await client.post(f"{API}/assistants/{aid}/conversations", json={}, headers=team.h(who))
    cid = c.json()["id"]
    async with get_sessionmaker()() as s:
        row = await waiting_request(
            s,
            conversation_id=uuid.UUID(cid),
            tool_name="mcp__caps__sql_query",
            tool_input={"sql": "UPDATE tickets SET status = 'closed'"},
            risk="high",
            rationale="writes",
        )
        return cid, str(row.id)


async def _decision_meta(approval_id: str) -> dict[str, Any]:
    async with get_sessionmaker()() as s:
        meta = await s.scalar(
            select(AuditLog.meta).where(
                AuditLog.action == "approval.approved", AuditLog.target_id == approval_id
            )
        )
    assert meta is not None
    return dict(meta)


async def test_a_tool_call_records_who_asked_and_whether_they_decided_it(
    client: AsyncClient, team: Team
) -> None:
    # Since Phase 9.1 nobody decides their own request: an approver does.
    _, own = await _pending(client, team, team.member)
    r = await decide(client, team.h(team.member), own, "approved")
    assert (r.status_code, r.json()["error"]["code"]) == (403, "cannot_decide_own")

    _, theirs = await _pending(client, team, team.member2)
    r = await decide(client, team.h(team.admin), theirs, "approved")
    assert r.status_code == 200, r.text
    meta = await _decision_meta(theirs)
    assert (meta["requested_by"], meta["decider_role"], meta["self_approved"]) == (
        team.member2.user_id,
        "admin",
        False,
    )


def test_the_migration_fills_in_who_asked(tmp_path: Path) -> None:
    db_path = tmp_path / "duties.db"
    assert _alembic(db_path, "upgrade", "c7b1d4e5f6a7").returncode == 0
    org, user, aid, cid, appr = (uuid.uuid4().hex for _ in range(5))
    db = sqlite3.connect(db_path)
    _insert(db, "organizations", id=org)
    _insert(db, "users", id=user, email="a@example.com")
    _insert(db, "assistants", id=aid, org_id=org)
    _insert(db, "conversations", id=cid, assistant_id=aid, org_id=org, created_by=user)
    _insert(db, "approvals", id=appr, conversation_id=cid, org_id=org, status="approved")
    db.commit()
    db.close()
    result = _alembic(db_path, "upgrade", "head")
    assert result.returncode == 0, result.stderr
    db = sqlite3.connect(db_path)
    assert db.execute("SELECT requested_by FROM approvals").fetchone() == (user,)
    db.close()


async def test_an_admin_alone_still_cannot_approve_their_own(
    client: AsyncClient, team: Team
) -> None:
    """Self-approval is the owner's alone: an admin left with no other
    reviewer (the owner's account switched off) waits for one."""
    admin = team.h(team.admin)
    aid = await _assistant(client, admin)
    n = await submit(client, aid, admin)
    await _deactivate(team.admin2, team.owner)
    r = await _approve(client, admin, aid, n, "Nobody else around")
    assert (r.status_code, _code(r)) == (403, "cannot_approve_own")
