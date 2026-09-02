from __future__ import annotations

from httpx import AsyncClient


async def test_register_creates_personal_org(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": "a@example.com", "password": "supersecret", "name": "A"},
    )
    assert resp.status_code == 201, resp.text
    tokens = resp.json()
    assert tokens["access_token"] and tokens["refresh_token"]

    me = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    assert me.status_code == 200
    body = me.json()
    assert body["user"]["email"] == "a@example.com"
    assert len(body["memberships"]) == 1
    assert body["memberships"][0]["role"] == "owner"


async def test_register_duplicate_email_conflicts(client: AsyncClient) -> None:
    payload = {"email": "dup@example.com", "password": "supersecret", "name": ""}
    first = await client.post("/api/v1/auth/register", json=payload)
    assert first.status_code == 201
    second = await client.post("/api/v1/auth/register", json=payload)
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "email_taken"


async def test_login_wrong_password_401(client: AsyncClient) -> None:
    await client.post(
        "/api/v1/auth/register",
        json={"email": "b@example.com", "password": "supersecret", "name": ""},
    )
    resp = await client.post(
        "/api/v1/auth/login", json={"email": "b@example.com", "password": "wrong"}
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_credentials"


async def test_refresh_rotation_and_reuse_detection(client: AsyncClient) -> None:
    reg = await client.post(
        "/api/v1/auth/register",
        json={"email": "c@example.com", "password": "supersecret", "name": ""},
    )
    refresh_1 = reg.json()["refresh_token"]

    r1 = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_1})
    assert r1.status_code == 200
    refresh_2 = r1.json()["refresh_token"]
    assert refresh_2 != refresh_1

    # Reusing the first (now-rotated) token must fail...
    reuse = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_1})
    assert reuse.status_code == 401
    assert reuse.json()["error"]["code"] == "refresh_reused"

    # ...and it must have revoked the whole family, killing refresh_2 too.
    r2 = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_2})
    assert r2.status_code == 401


async def test_me_requires_token(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401


async def test_protected_route_rejects_garbage_token(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/auth/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert resp.status_code == 401
