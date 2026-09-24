"""The invite link, end to end (audit: "no invite-accept UI").

Both endpoints existed but nothing could use them: no page at the URL the API
hands out, no way to see what an invite is for before signing in, and the
accept response carried no org id for the UI to switch to.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.db.session import get_sessionmaker
from app.models.invite import Invite
from httpx import AsyncClient
from sqlalchemy import update
from tests.test_orgs import Actor, _register

pytestmark = pytest.mark.anyio


async def _invite(client: AsyncClient, owner: Actor, email: str) -> tuple[str, str, dict]:
    org = (await client.post("/api/v1/orgs", json={"name": "Acme"}, headers=owner.headers)).json()
    r = await client.post(
        f"/api/v1/orgs/{org['id']}/invites", json={"email": email}, headers=owner.headers
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["accept_url"].endswith("/invites/" + body["accept_url"].rsplit("/", 1)[-1])
    return org["id"], body["accept_url"].rsplit("/", 1)[-1], body


async def test_an_invite_can_be_previewed_without_signing_in(client: AsyncClient) -> None:
    owner = await _register(client, "own1@example.com")
    _, token, _ = await _invite(client, owner, "new1@example.com")
    r = await client.get(f"/api/v1/invites/{token}")  # no Authorization header
    assert r.status_code == 200
    assert r.json() | {"expires_at": None} == {
        "org_name": "Acme",
        "email": "new1@example.com",
        "role": "member",
        "status": "pending",
        "expires_at": None,
    }


async def test_accepting_returns_the_org_and_it_is_listed_at_once(client: AsyncClient) -> None:
    owner = await _register(client, "own2@example.com")
    org_id, token, _ = await _invite(client, owner, "new2@example.com")
    invitee = await _register(client, "new2@example.com")

    r = await client.post(f"/api/v1/invites/{token}/accept", headers=invitee.headers)
    assert r.status_code == 200
    assert r.json() == {"org_id": org_id, "org_name": "Acme", "role": "member"}

    orgs = (await client.get("/api/v1/orgs", headers=invitee.headers)).json()["items"]
    assert org_id in {o["id"] for o in orgs}
    assert (await client.get(f"/api/v1/invites/{token}")).json()["status"] == "accepted"
    again = await client.post(f"/api/v1/invites/{token}/accept", headers=invitee.headers)
    assert again.status_code == 404, "single use"


async def test_the_wrong_account_cannot_accept(client: AsyncClient) -> None:
    owner = await _register(client, "own3@example.com")
    _, token, _ = await _invite(client, owner, "meant-for@example.com")
    other = await _register(client, "someone-else@example.com")
    r = await client.post(f"/api/v1/invites/{token}/accept", headers=other.headers)
    assert r.status_code == 403


async def test_an_expired_invite_says_so(client: AsyncClient) -> None:
    owner = await _register(client, "own4@example.com")
    _, token, body = await _invite(client, owner, "late@example.com")
    async with get_sessionmaker()() as s:
        await s.execute(
            update(Invite)
            .where(Invite.id == uuid.UUID(body["id"]))
            .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
        )
        await s.commit()
    assert (await client.get(f"/api/v1/invites/{token}")).json()["status"] == "expired"
    late = await _register(client, "late@example.com")
    assert (
        await client.post(f"/api/v1/invites/{token}/accept", headers=late.headers)
    ).status_code == 400


async def test_an_unknown_token_is_a_404(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/invites/not-a-real-token")).status_code == 404


async def test_the_invite_audit_entry_names_the_invite(client: AsyncClient) -> None:
    owner = await _register(client, "own5@example.com")
    org_id, _, body = await _invite(client, owner, "audited@example.com")
    log = (await client.get(f"/api/v1/orgs/{org_id}/audit-log", headers=owner.headers)).json()
    entry = next(e for e in log["items"] if e["action"] == "org.invite.create")
    assert entry["target_id"] == body["id"], "it used to say '(pending)'"
