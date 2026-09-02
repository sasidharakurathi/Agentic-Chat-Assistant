from __future__ import annotations

from dataclasses import dataclass

from httpx import AsyncClient


@dataclass
class Actor:
    headers: dict[str, str]
    user_id: str


async def _register(client: AsyncClient, email: str) -> Actor:
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "supersecret", "name": email.split("@")[0]},
    )
    assert resp.status_code == 201, resp.text
    access = resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {access}"}
    me = await client.get("/api/v1/auth/me", headers=headers)
    return Actor(headers=headers, user_id=me.json()["user"]["id"])


async def test_list_and_create_orgs(client: AsyncClient, auth_headers: dict[str, str]) -> None:
    listing = await client.get("/api/v1/orgs", headers=auth_headers)
    assert listing.status_code == 200
    assert len(listing.json()) == 1  # personal org from registration

    created = await client.post("/api/v1/orgs", json={"name": "Acme Inc"}, headers=auth_headers)
    assert created.status_code == 201
    assert created.json()["slug"] == "acme-inc"
    assert len((await client.get("/api/v1/orgs", headers=auth_headers)).json()) == 2


async def test_invite_flow_and_rbac(client: AsyncClient) -> None:
    owner = await _register(client, "owner@example.com")
    member = await _register(client, "member@example.com")

    org = (await client.post("/api/v1/orgs", json={"name": "Team"}, headers=owner.headers)).json()

    inv = await client.post(
        f"/api/v1/orgs/{org['id']}/invites",
        json={"email": "member@example.com", "role": "member"},
        headers=owner.headers,
    )
    assert inv.status_code == 201
    token = inv.json()["accept_url"].rsplit("/", 1)[-1]

    joined = await client.post(f"/api/v1/invites/{token}/accept", headers=member.headers)
    assert joined.status_code == 200

    members = await client.get(f"/api/v1/orgs/{org['id']}/members", headers=member.headers)
    assert members.status_code == 200
    assert {m["role"] for m in members.json()} == {"owner", "member"}

    # member (not admin) cannot promote the owner
    bad = await client.patch(
        f"/api/v1/orgs/{org['id']}/members/{owner.user_id}",
        json={"role": "admin"},
        headers=member.headers,
    )
    assert bad.status_code == 403
    assert bad.json()["error"]["code"] == "insufficient_role"

    # owner can promote the member to admin
    good = await client.patch(
        f"/api/v1/orgs/{org['id']}/members/{member.user_id}",
        json={"role": "admin"},
        headers=owner.headers,
    )
    assert good.status_code == 200
    assert good.json()["role"] == "admin"

    # audit log now readable by the (newly) admin member
    audit = await client.get(f"/api/v1/orgs/{org['id']}/audit-log", headers=member.headers)
    assert audit.status_code == 200
    actions = {e["action"] for e in audit.json()}
    assert "org.invite.accept" in actions
    assert "org.member.role_change" in actions


async def test_non_member_gets_404_for_org(client: AsyncClient) -> None:
    a = await _register(client, "a2@example.com")
    org = (await client.post("/api/v1/orgs", json={"name": "Private"}, headers=a.headers)).json()
    b = await _register(client, "b2@example.com")
    resp = await client.get(f"/api/v1/orgs/{org['id']}/members", headers=b.headers)
    assert resp.status_code == 404


async def test_last_owner_cannot_be_demoted(client: AsyncClient) -> None:
    owner = await _register(client, "solo@example.com")
    org = (await client.post("/api/v1/orgs", json={"name": "Solo"}, headers=owner.headers)).json()
    resp = await client.patch(
        f"/api/v1/orgs/{org['id']}/members/{owner.user_id}",
        json={"role": "member"},
        headers=owner.headers,
    )
    assert resp.status_code == 400
