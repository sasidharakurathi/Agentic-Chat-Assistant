"""People joining, leaving and coming back (Phase 7b.4).

Signing up with an invite joins that organisation only. Deactivating
someone ends their access and every session, hands their assistants on,
withdraws their co-editor grants, closes their pending approvals and
revokes the invites they sent; reactivating brings back the person, not
those. An org keeps an active owner. Invites can be listed and revoked,
one is open per address, and one from someone who can no longer invite
opens nothing. Co-editors edit an assistant besides its creator.
"""

from __future__ import annotations

import sqlite3
import uuid
from typing import Any

import pytest
from app.config import settings
from app.db.session import get_sessionmaker
from app.models.approval import Approval, ApprovalStatus
from app.models.assistant import Assistant
from app.models.audit_log import AuditLog
from app.models.invite import Invite
from httpx import AsyncClient
from sqlalchemy import select
from tests.pending import waiting_request
from tests.test_migrations import _alembic
from tests.test_version_review_migration import _insert

from test_rbac_escalation import Team, _team  # type: ignore[import-not-found]

pytestmark = pytest.mark.anyio

API = "/api/v1"


@pytest.fixture
async def team(client: AsyncClient) -> Team:
    return await _team(client)


async def _invite(client: AsyncClient, team: Team, email: str, role: str = "member") -> dict:
    r = await client.post(
        f"{API}/orgs/{team.org_id}/invites",
        json={"email": email, "role": role},
        headers=team.h(team.owner),
    )
    assert r.status_code == 201, r.text
    return dict(r.json())


def _token(invite: dict) -> str:
    return str(invite["accept_url"].rsplit("/", 1)[-1])


async def _sign_up(client: AsyncClient, email: str, token: str | None) -> Any:
    return await client.post(
        f"{API}/auth/register",
        json={"email": email, "password": "supersecret", "name": "New", "invite_token": token},
    )


async def _deactivate(
    client: AsyncClient, team: Team, who: Any, by: Any = None, **body: Any
) -> Any:
    return await client.post(
        f"{API}/orgs/{team.org_id}/members/{who.user_id}:deactivate",
        json=body,
        headers=team.h(by or team.owner),
    )


async def _actions(action: str) -> list[dict[str, Any]]:
    async with get_sessionmaker()() as s:
        return [
            dict(m) for m in await s.scalars(select(AuditLog.meta).where(AuditLog.action == action))
        ]


# ── joining ──────────────────────────────────────────────────


async def test_signing_up_with_an_invite_joins_that_org_only(
    client: AsyncClient, team: Team
) -> None:
    """No personal org beside it, and no second Join step to forget."""
    invite = await _invite(client, team, "newcomer@example.com", "admin")
    r = await _sign_up(client, "newcomer@example.com", _token(invite))
    assert r.status_code == 201, r.text
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    me = (await client.get(f"{API}/auth/me", headers=h)).json()
    assert me["memberships"] == [{"org_id": team.org_id, "role": "admin"}]
    preview = (await client.get(f"{API}/invites/{_token(invite)}")).json()
    assert preview["status"] == "accepted"


# ── leaving ──────────────────────────────────────────────────


async def test_deactivating_someone_ends_their_access_and_hands_on_their_work(
    client: AsyncClient, team: Team
) -> None:
    member = team.h(team.member)
    a = await client.post(f"{API}/assistants", json={"name": "Theirs"}, headers=member)
    aid = a.json()["id"]
    other = await client.post(
        f"{API}/assistants", json={"name": "Shared"}, headers=team.h(team.owner)
    )
    await client.post(
        f"{API}/assistants/{other.json()['id']}/editors",
        json={"user_id": team.member.user_id},
        headers=team.h(team.owner),
    )
    c = await client.post(
        f"{API}/assistants/{aid}/conversations", json={"mode": "preview"}, headers=member
    )
    async with get_sessionmaker()() as s:
        waiting = await waiting_request(
            s,
            conversation_id=uuid.UUID(c.json()["id"]),
            tool_name="mcp__caps__sql_query",
            tool_input={"sql": "UPDATE t SET x = 1"},
            risk="high",
            rationale="writes",
        )
    refresh = (
        await client.post(
            f"{API}/auth/login", json={"email": "member@example.com", "password": "supersecret"}
        )
    ).json()["refresh_token"]

    leaving = (
        await client.get(
            f"{API}/orgs/{team.org_id}/members/{team.member.user_id}/leaving",
            headers=team.h(team.admin),
        )
    ).json()
    assert [x["name"] for x in leaving["assistants"]] == ["Theirs"]
    assert [x["name"] for x in leaving["editor_of"]] == ["Shared"]
    assert leaving["pending_approvals"] == 1

    r = await _deactivate(client, team, team.member, by=team.admin)
    assert r.status_code == 200, r.text
    assert (r.json()["is_active"], r.json()["deactivated_at"] is not None) == (False, True)

    # Gone from the org at once, and signed out everywhere.
    assert (await client.get(f"{API}/assistants/{aid}", headers=member)).status_code == 404
    renewed = await client.post(f"{API}/auth/refresh", json={"refresh_token": refresh})
    assert renewed.status_code == 401
    me = (await client.get(f"{API}/auth/me", headers=team.member.headers)).json()
    assert team.org_id not in {m["org_id"] for m in me["memberships"]}

    async with get_sessionmaker()() as s:
        moved = await s.get(Assistant, uuid.UUID(aid))
        assert moved is not None and str(moved.created_by) == team.admin.user_id
        closed = await s.get(Approval, waiting.id)
        assert closed is not None and closed.status is ApprovalStatus.cancelled
        assert closed.sealed_ciphertext is None, "what it would have run is gone"
    editors = (
        await client.get(
            f"{API}/assistants/{other.json()['id']}/editors", headers=team.h(team.owner)
        )
    ).json()
    assert editors == [], "their co-editor grant went with them"
    (transfer,) = await _actions("assistant.transfer")
    assert transfer == {
        "from": team.member.user_id,
        "to": team.admin.user_id,
        "reason": "deactivated",
    }
    (left,) = await _actions("org.member.deactivate")
    assert (left["approvals_closed"], left["editor_grants_removed"], left["assistants"]) == (
        1,
        1,
        [aid],
    )


async def test_assistants_go_to_whoever_the_admin_names(client: AsyncClient, team: Team) -> None:
    a = await client.post(f"{API}/assistants", json={"name": "X"}, headers=team.h(team.member))
    r = await _deactivate(client, team, team.member, transfer_to=team.member2.user_id)
    assert r.status_code == 200, r.text
    async with get_sessionmaker()() as s:
        moved = await s.get(Assistant, uuid.UUID(a.json()["id"]))
        assert moved is not None and str(moved.created_by) == team.member2.user_id
    r = await _deactivate(client, team, team.member2, transfer_to=team.member.user_id)
    assert (r.status_code, r.json()["error"]["code"]) == (400, "invalid_transfer"), (
        "not to a leaver"
    )


async def test_an_account_with_no_other_org_is_switched_off(
    client: AsyncClient, team: Team
) -> None:
    invite = await _invite(client, team, "only-here@example.com")
    signed = (await _sign_up(client, "only-here@example.com", _token(invite))).json()
    me = (
        await client.get(
            f"{API}/auth/me", headers={"Authorization": f"Bearer {signed['access_token']}"}
        )
    ).json()

    class Person:
        user_id = me["user"]["id"]

    assert (await _deactivate(client, team, Person)).status_code == 200
    login = await client.post(
        f"{API}/auth/login", json={"email": "only-here@example.com", "password": "supersecret"}
    )
    assert (login.status_code, login.json()["error"]["code"]) == (401, "account_disabled")


async def test_who_may_remove_whom(client: AsyncClient, team: Team) -> None:
    r = await _deactivate(client, team, team.admin2, by=team.admin)
    assert (r.status_code, r.json()["error"]["code"]) == (403, "role_change_not_allowed")
    r = await _deactivate(client, team, team.owner, by=team.admin)
    assert r.status_code == 403
    r = await _deactivate(client, team, team.owner, by=team.owner)
    assert (r.status_code, r.json()["error"]["code"]) == (403, "cannot_deactivate_self")
    r = await _deactivate(client, team, team.member, by=team.member2)
    assert r.status_code == 403, "members remove nobody"
    assert (await _deactivate(client, team, team.admin2, by=team.owner)).status_code == 200


async def test_an_org_keeps_an_active_owner(client: AsyncClient, team: Team) -> None:
    owner = team.h(team.owner)
    await client.patch(
        f"{API}/orgs/{team.org_id}/members/{team.admin.user_id}",
        json={"role": "owner"},
        headers=owner,
    )
    # Two owners: one may remove the other...
    assert (await _deactivate(client, team, team.admin)).status_code == 200
    # ...and the deactivated one doesn't count: the last active owner stays.
    r = await client.patch(
        f"{API}/orgs/{team.org_id}/members/{team.owner.user_id}",
        json={"role": "admin"},
        headers=owner,
    )
    assert (r.status_code, r.json()["error"]["code"]) == (400, "last_owner")


async def test_reactivating_brings_back_the_person_not_their_old_assistants(
    client: AsyncClient, team: Team
) -> None:
    a = await client.post(f"{API}/assistants", json={"name": "X"}, headers=team.h(team.member))
    await _deactivate(client, team, team.member)
    r = await client.post(
        f"{API}/orgs/{team.org_id}/members/{team.member.user_id}:reactivate",
        headers=team.h(team.owner),
    )
    assert (r.status_code, r.json()["is_active"]) == (200, True)
    detail = await client.get(f"{API}/assistants/{a.json()['id']}", headers=team.h(team.member))
    assert detail.status_code == 200, "back in the org"
    assert detail.json()["can_edit"] is False, "the assistant stayed with its new owner"
    assert len(await _actions("org.member.reactivate")) == 1


# ── invites ──────────────────────────────────────────────────


async def test_invites_are_listed_revocable_and_one_per_address(
    client: AsyncClient, team: Team
) -> None:
    first = await _invite(client, team, "pat@example.com")
    second = await _invite(client, team, "pat@example.com")
    assert (await client.get(f"{API}/invites/{_token(first)}")).json()["status"] == "revoked"
    listed = (
        await client.get(f"{API}/orgs/{team.org_id}/invites", headers=team.h(team.admin))
    ).json()
    assert [i["id"] for i in listed if i["email"] == "pat@example.com"] == [second["id"]]

    r = await client.delete(
        f"{API}/orgs/{team.org_id}/invites/{second['id']}", headers=team.h(team.admin)
    )
    assert r.status_code == 200, r.text
    assert (await client.get(f"{API}/invites/{_token(second)}")).json()["status"] == "revoked"
    r = await _sign_up(client, "pat@example.com", _token(second))
    me = (
        await client.get(
            f"{API}/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"}
        )
    ).json()
    assert team.org_id not in {m["org_id"] for m in me["memberships"]}, (
        "a revoked link joins nothing"
    )


async def test_an_invite_from_someone_who_can_no_longer_invite_opens_nothing(
    client: AsyncClient, team: Team
) -> None:
    r = await client.post(
        f"{API}/orgs/{team.org_id}/invites",
        json={"email": "via-admin@example.com", "role": "admin"},
        headers=team.h(team.admin),
    )
    token = _token(r.json())
    await client.patch(
        f"{API}/orgs/{team.org_id}/members/{team.admin.user_id}",
        json={"role": "member"},
        headers=team.h(team.owner),
    )
    assert (await client.get(f"{API}/invites/{token}")).json()["status"] == "revoked"
    joiner = await _sign_up(client, "via-admin@example.com", None)
    accepted = await client.post(
        f"{API}/invites/{token}/accept",
        headers={"Authorization": f"Bearer {joiner.json()['access_token']}"},
    )
    assert (accepted.status_code, accepted.json()["error"]["code"]) == (
        403,
        "invite_no_longer_valid",
    )


async def test_a_leaver_is_reactivated_not_invited_and_their_invites_go(
    client: AsyncClient, team: Team
) -> None:
    sent = await client.post(
        f"{API}/orgs/{team.org_id}/invites",
        json={"email": "friend@example.com", "role": "member"},
        headers=team.h(team.admin),
    )
    await _deactivate(client, team, team.admin)
    assert (await client.get(f"{API}/invites/{_token(sent.json())}")).json()["status"] == "revoked"
    async with get_sessionmaker()() as s:
        row = await s.get(Invite, uuid.UUID(sent.json()["id"]))
        assert row is not None and row.revoked_at is not None
    r = await client.post(
        f"{API}/orgs/{team.org_id}/invites",
        json={"email": "admin@example.com", "role": "admin"},
        headers=team.h(team.owner),
    )
    assert (r.status_code, r.json()["error"]["code"]) == (409, "member_deactivated")


async def test_an_invite_lasts_as_long_as_the_setting(
    client: AsyncClient, team: Team, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "invite_ttl_days", 2)
    invite = await _invite(client, team, "soon@example.com")
    async with get_sessionmaker()() as s:
        row = await s.get(Invite, uuid.UUID(invite["id"]))
        assert row is not None
        assert 47 * 3600 < (row.expires_at - row.created_at).total_seconds() < 49 * 3600


# ── co-editors ───────────────────────────────────────────────


async def test_a_co_editor_edits_but_does_not_name_others(client: AsyncClient, team: Team) -> None:
    creator, helper = team.h(team.member), team.h(team.member2)
    a = (await client.post(f"{API}/assistants", json={"name": "Desk"}, headers=creator)).json()
    url = f"{API}/assistants/{a['id']}"
    cfg = a["draft_config"]
    assert (await client.put(f"{url}/draft-config", json=cfg, headers=helper)).status_code == 403

    r = await client.post(f"{url}/editors", json={"user_id": team.member2.user_id}, headers=creator)
    assert r.status_code == 201, r.text
    assert [e["user_id"] for e in r.json()] == [team.member2.user_id]
    assert (await client.put(f"{url}/draft-config", json=cfg, headers=helper)).status_code == 200
    assert (await client.get(url, headers=helper)).json()["can_edit"] is True
    preview = await client.post(f"{url}/conversations", json={"mode": "preview"}, headers=helper)
    assert preview.status_code == 201, "a co-editor previews"

    r = await client.post(f"{url}/editors", json={"user_id": team.admin.user_id}, headers=helper)
    assert (r.status_code, r.json()["error"]["code"]) == (403, "not_assistant_owner")
    r = await client.post(f"{url}/editors", json={"user_id": str(uuid.uuid4())}, headers=creator)
    assert r.status_code == 404

    r = await client.delete(f"{url}/editors/{team.member2.user_id}", headers=team.h(team.admin))
    assert r.status_code == 200
    assert (await client.put(f"{url}/draft-config", json=cfg, headers=helper)).status_code == 403
    assert {m["user_id"] for m in await _actions("assistant.editor.add")} == {team.member2.user_id}


def test_the_migration_keeps_the_newest_open_invite(tmp_path: Any) -> None:
    db_path = tmp_path / "people.db"
    assert _alembic(db_path, "upgrade", "c7b3e1f2a3b4").returncode == 0
    org, old, new = uuid.uuid4().hex, uuid.uuid4().hex, uuid.uuid4().hex
    db = sqlite3.connect(db_path)
    _insert(db, "organizations", id=org)
    for iid, when in ((old, "2026-01-01 00:00:00"), (new, "2026-02-01 00:00:00")):
        _insert(
            db,
            "invites",
            id=iid,
            org_id=org,
            email="dup@example.com",
            token_hash=iid,
            created_at=when,
        )
    db.commit()
    db.close()
    result = _alembic(db_path, "upgrade", "head")
    assert result.returncode == 0, result.stderr
    db = sqlite3.connect(db_path)
    revoked = dict(db.execute("SELECT id, revoked_at IS NOT NULL FROM invites").fetchall())
    assert revoked == {old: 1, new: 0}
    db.close()


async def test_a_switched_off_account_renews_nothing(client: AsyncClient, team: Team) -> None:
    from app.models.user import User

    refresh = (
        await client.post(
            f"{API}/auth/login", json={"email": "member@example.com", "password": "supersecret"}
        )
    ).json()["refresh_token"]
    async with get_sessionmaker()() as s:
        user = await s.get(User, uuid.UUID(team.member.user_id))
        assert user is not None
        user.is_active = False
        await s.commit()
    renewed = await client.post(f"{API}/auth/refresh", json={"refresh_token": refresh})
    assert renewed.status_code == 401
