"""Migration and rollout (Phase 7b.8).

The upgrade sorts out the personal orgs made at sign-up before 7b.4: one
that others joined becomes the team's, an empty one whose people have
somewhere else to go is archived, and the rest are left for the operator.
An archived org reaches nobody. In single-org mode nobody creates a second
organisation and sign-up is by invite. The owner renames the organisation
and sets its switches, each change recorded, and the switches take effect.
"""

from __future__ import annotations

import sqlite3
import uuid
import warnings
from pathlib import Path
from typing import Any

import pytest
from app import preflight
from app.config import settings
from app.db.session import get_sessionmaker
from app.mail import backends
from app.models.audit_log import AuditLog
from app.models.budget import Budget
from app.models.organization import Organization
from app.services import rollout
from httpx import AsyncClient
from sqlalchemy import select
from tests.test_deploy import _invite, _production, _register
from tests.test_migrations import _alembic
from tests.test_version_review_migration import _insert
from tests.versions import submit

from test_rbac_escalation import Team, _team  # type: ignore[import-not-found]

pytestmark = pytest.mark.anyio

API = "/api/v1"
BEFORE = "c7b7a2b3c4d5"
THIS = "c7b8a1b2c3d4"


@pytest.fixture
async def team(client: AsyncClient) -> Team:
    return await _team(client)


@pytest.fixture(autouse=True)
def _empty_outbox() -> Any:
    backends.outbox.clear()
    yield
    backends.outbox.clear()


# ── the migration ────────────────────────────────────────────


def test_the_upgrade_sorts_out_the_personal_orgs(tmp_path: Path) -> None:
    db_path = tmp_path / "rollout.db"
    assert (r := _alembic(db_path, "upgrade", BEFORE)).returncode == 0, r.stderr
    u1, u2, u3, u4 = (uuid.uuid4().hex for _ in range(4))
    shared, landed, alone, busy = (uuid.uuid4().hex for _ in range(4))
    db = sqlite3.connect(db_path)
    for u in (u1, u2, u3, u4):
        _insert(db, "users", id=u)
    for org in (shared, landed, alone, busy):
        _insert(db, "organizations", id=org, is_personal=1)
    # The owner's sign-up org, which the others were invited into.
    for u in (u1, u2, u4):
        _insert(db, "memberships", org_id=shared, user_id=u)
    _insert(db, "memberships", org_id=landed, user_id=u2)  # empty; u2 is in `shared`
    _insert(db, "memberships", org_id=alone, user_id=u3)  # empty; u3 has nowhere else
    _insert(db, "memberships", org_id=busy, user_id=u4)  # u4 built something here
    _insert(db, "assistants", org_id=busy)
    db.commit()
    db.close()

    assert (r := _alembic(db_path, "upgrade", THIS)).returncode == 0, r.stderr
    db = sqlite3.connect(db_path)
    state = {
        org: (personal, archived is not None, reason)
        for org, personal, archived, reason in db.execute(
            "SELECT id, is_personal, archived_at, archived_reason FROM organizations"
        )
    }
    assert state[shared] == (0, False, None), "others joined it: it is the team's"
    assert state[landed][:2] == (1, True) and "upgrade" in state[landed][2]
    assert state[alone] == (1, False, None), "archiving it would lock its person out"
    assert state[busy] == (1, False, None), "it has an assistant: the operator decides"
    actions = {
        (org, action) for org, action in db.execute("SELECT target_id, action FROM audit_log")
    }
    assert (str(uuid.UUID(shared)), "org.became_team") in actions
    assert (str(uuid.UUID(landed)), "org.archive") in actions
    assert {a for _o, a in actions} == {"org.became_team", "org.archive"}
    assert db.execute("SELECT settings FROM organizations LIMIT 1").fetchone()[0] == "{}"
    db.close()

    assert (r := _alembic(db_path, "downgrade", BEFORE)).returncode == 0, r.stderr
    assert (r := _alembic(db_path, "upgrade", THIS)).returncode == 0, r.stderr


# ── archived organisations ───────────────────────────────────


async def _archive(org_id: str) -> None:
    async with get_sessionmaker()() as s:
        await rollout.archive(s, uuid.UUID(org_id), reason="A test")


async def test_an_archived_org_reaches_nobody(client: AsyncClient, team: Team) -> None:
    await _archive(team.org_id)
    r = await client.get(f"{API}/orgs/{team.org_id}/members", headers=team.h(team.owner))
    assert r.status_code == 404
    listed = await client.get(f"{API}/orgs", headers=team.owner.headers)
    assert team.org_id not in [o["id"] for o in listed.json()["items"]]
    me = await client.get(f"{API}/auth/me", headers=team.owner.headers)
    assert team.org_id not in [m["org_id"] for m in me.json()["memberships"]]
    r = await client.get(f"{API}/assistants", headers=team.h(team.member))
    assert r.status_code == 404

    async with get_sessionmaker()() as s:
        await rollout.unarchive(s, uuid.UUID(team.org_id))
    r = await client.get(f"{API}/orgs/{team.org_id}/members", headers=team.h(team.owner))
    assert r.status_code == 200
    async with get_sessionmaker()() as s:
        actions = list(
            await s.scalars(
                select(AuditLog.action)
                .where(AuditLog.target_id == team.org_id)
                .execution_options(all_orgs=True)
            )
        )
    assert {"org.archive", "org.unarchive"} <= set(actions)


async def test_an_invite_into_an_archived_org_is_dead(client: AsyncClient, team: Team) -> None:
    made = await client.post(
        f"{API}/orgs/{team.org_id}/invites",
        json={"email": "late@example.com", "role": "member"},
        headers=team.h(team.owner),
    )
    token = made.json()["accept_url"].rsplit("/", 1)[-1]
    await _archive(team.org_id)
    assert (await client.get(f"{API}/invites/{token}")).status_code == 404


async def test_the_operator_sees_who_would_be_stranded(client: AsyncClient, team: Team) -> None:
    async with get_sessionmaker()() as s:
        left = await rollout.stranded(s, uuid.UUID(team.org_id))
    # Each person in the team also has the personal org they signed up with.
    assert left == []
    me = await client.get(f"{API}/auth/me", headers=team.member.headers)
    own = next(m["org_id"] for m in me.json()["memberships"] if m["org_id"] != team.org_id)
    async with get_sessionmaker()() as s:
        await rollout.archive(s, uuid.UUID(own), reason="A test")
        assert await rollout.stranded(s, uuid.UUID(team.org_id)) == ["member@example.com"]


async def test_freezing_stops_spend_and_keeps_the_org(client: AsyncClient, team: Team) -> None:
    async with get_sessionmaker()() as s:
        await rollout.freeze(s, uuid.UUID(team.org_id))
        limits = list(
            await s.scalars(
                select(Budget.limit_usd)
                .where(Budget.org_id == uuid.UUID(team.org_id))
                .execution_options(all_orgs=True)
            )
        )
    assert sorted(float(x) for x in limits) == [0.0, 0.0]
    r = await client.get(f"{API}/orgs/{team.org_id}/members", headers=team.h(team.owner))
    assert r.status_code == 200, "frozen, not archived"


async def test_the_report_flags_what_needs_a_decision(
    client: AsyncClient, team: Team, monkeypatch: pytest.MonkeyPatch
) -> None:
    me = await client.get(f"{API}/auth/me", headers=team.member.headers)
    own = next(m["org_id"] for m in me.json()["memberships"] if m["org_id"] != team.org_id)
    await client.post(
        f"{API}/assistants", json={"name": "Side"}, headers={**team.member.headers, "X-Org-Id": own}
    )
    monkeypatch.setattr(settings, "org_mode", "single")
    async with get_sessionmaker()() as s:
        found = await rollout.report(s)
    warned = " ".join(text for level, text in found.notes if level == "warn")
    assert "personal organisation with assistants" in warned
    assert "ORG_MODE=single" in warned
    line = next(o for o in found.orgs if str(o.id) == team.org_id)
    assert (line.personal, line.members, line.owners) == (False, 5, ["owner@example.com"])


# ── single-org mode ──────────────────────────────────────────


async def test_single_mode_has_one_organisation(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "org_mode", "single")
    monkeypatch.setattr(settings, "registration", "open")
    first = await _register(client, "owner@example.com")
    assert first.status_code == 201
    owner = {"Authorization": f"Bearer {first.json()['access_token']}"}
    (org,) = (await client.get(f"{API}/orgs", headers=owner)).json()["items"]
    assert org["is_personal"] is False, "the first account's org is the company's"

    r = await client.post(f"{API}/orgs", json={"name": "Another"}, headers=owner)
    assert (r.status_code, r.json()["error"]["code"]) == (403, "org_creation_closed")

    stranger = await _register(client, "stranger@example.com")
    assert stranger.json()["error"]["code"] == "registration_by_invite", "open is ignored"
    token = await _invite(client, owner, "friend@example.com")
    friend = await _register(client, "friend@example.com", invite_token=token)
    assert friend.status_code == 201
    h = {"Authorization": f"Bearer {friend.json()['access_token']}"}
    assert [o["id"] for o in (await client.get(f"{API}/orgs", headers=h)).json()["items"]] == [
        org["id"]
    ]


def test_the_preflight_asks_for_single_mode_in_production() -> None:
    report = preflight.Report()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        preflight.review(_production(org_mode="multi"), report)
    assert [f.name for f in report.findings if f.level == "warn"] == ["ORG_MODE"]


# ── settings and switches ────────────────────────────────────


async def test_admins_read_the_settings_and_only_the_owner_changes_them(
    client: AsyncClient, team: Team
) -> None:
    url = f"{API}/orgs/{team.org_id}/settings"
    r = await client.get(url, headers=team.h(team.admin))
    assert r.status_code == 200
    assert r.json() | {"name": "-"} == {
        "name": "-",
        "review_emails": True,
        "decision_emails": True,
        # Phase 8: the switches and settings added since.
        "approval_emails": True,
        "google_auto_join": False,
        "sole_reviewer_self_approval": True,
        "offline_help": "",
        "privacy_contact": "",
        "retention_days": 365,
        # Phase 9.1: how long a request waits, and the reminder.
        "action_expiry_hours": 72,
        "action_reminder_hours": 24,
        # Phase 9.3: no internal destinations until the owner lists some.
        "internal_destinations": [],
        # Phase 9.7: no cap on builder testing until the owner sets one.
        "builder_monthly_usd": None,
        "org_mode": "multi",
        "can_edit": False,
    }
    assert (await client.get(url, headers=team.h(team.member))).status_code == 403
    r = await client.patch(url, json={"name": "Acme"}, headers=team.h(team.admin))
    assert r.status_code == 403

    r = await client.patch(
        url, json={"name": "Acme", "review_emails": False}, headers=team.h(team.owner)
    )
    assert r.status_code == 200, r.text
    assert (r.json()["name"], r.json()["review_emails"], r.json()["can_edit"]) == (
        "Acme",
        False,
        True,
    )
    listed = await client.get(f"{API}/orgs", headers=team.owner.headers)
    assert "Acme" in [o["name"] for o in listed.json()["items"]]
    async with get_sessionmaker()() as s:
        (meta,) = list(
            await s.scalars(
                select(AuditLog.meta)
                .where(AuditLog.action == "org.settings.update")
                .execution_options(all_orgs=True)
            )
        )
    assert meta["changed"]["name"]["to"] == "Acme"
    assert meta["changed"]["review_emails"] == {"from": True, "to": False}
    # Sending the same again changes nothing, and records nothing.
    await client.patch(url, json={"review_emails": False}, headers=team.h(team.owner))
    async with get_sessionmaker()() as s:
        count = len(
            list(
                await s.scalars(
                    select(AuditLog.id)
                    .where(AuditLog.action == "org.settings.update")
                    .execution_options(all_orgs=True)
                )
            )
        )
    assert count == 1


async def test_the_email_switches_are_obeyed(client: AsyncClient, team: Team) -> None:
    await client.patch(
        f"{API}/orgs/{team.org_id}/settings",
        json={"review_emails": False, "decision_emails": False},
        headers=team.h(team.owner),
    )
    builder = team.h(team.member)
    aid = (await client.post(f"{API}/assistants", json={"name": "D"}, headers=builder)).json()["id"]
    n = await submit(client, aid, builder)
    await client.post(
        f"{API}/assistants/{aid}/versions/{n}/approve",
        json={"note": ""},
        headers=team.h(team.admin),
    )
    kinds = {m.kind for m in backends.outbox}
    assert not kinds & {"version_submitted", "version_reviewed"}


async def test_without_self_approval_the_sole_owner_needs_a_reviewer(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    org_id = org_headers["X-Org-Id"]
    r = await client.patch(
        f"{API}/orgs/{org_id}/settings",
        json={"sole_reviewer_self_approval": False},
        headers=org_headers,
    )
    assert r.status_code == 200, r.text
    aid = (await client.post(f"{API}/assistants", json={"name": "D"}, headers=org_headers)).json()[
        "id"
    ]
    n = await submit(client, aid, org_headers)
    r = await client.post(
        f"{API}/assistants/{aid}/versions/{n}/approve",
        json={"note": "Tested; only reviewer"},
        headers=org_headers,
    )
    assert (r.status_code, r.json()["error"]["code"]) == (403, "cannot_approve_own")


async def test_an_unknown_stored_key_is_kept(team: Team) -> None:
    from app.services import org_settings

    async with get_sessionmaker()() as s:
        org = await s.get(Organization, uuid.UUID(team.org_id))
        assert org is not None
        org.settings = {"a_later_switch": 3, "review_emails": False}
        await s.commit()
        await org_settings.update(
            s,
            org_id=org.id,
            actor_id=uuid.UUID(team.owner.user_id),
            name=None,
            changes={"decision_emails": False},
            ip=None,
        )
        org = await s.get(Organization, uuid.UUID(team.org_id))
        assert org is not None
        assert org.settings == {
            "a_later_switch": 3,
            "review_emails": False,
            "decision_emails": False,
        }
