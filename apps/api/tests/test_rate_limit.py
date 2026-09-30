"""Rate limiting (task 5.8): token buckets per IP, user and org.

The API tests use the in-process buckets (the unit suite has no Redis) with
tiny limits; the Redis script itself is tested against a real Redis in the
integration tier, including that concurrent requests can't share a token.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest
from app.api import ratelimit as api_rl
from app.config import settings
from app.db.redis import get_redis
from app.security import ratelimit
from app.security.ratelimit import Limit, _Memory
from httpx import AsyncClient
from redis.exceptions import ConnectionError as RedisConnectionError

from test_orgs import _register  # type: ignore[import-not-found]

# ── the bucket ───────────────────────────────────────────────


def test_limits_read_as_requests_per_seconds() -> None:
    assert Limit.parse("30/60") == Limit(30, 60.0)
    assert Limit.parse("30/60").rate == 0.5
    for bad in ("0/60", "5/0", "ten/60"):
        with pytest.raises(ValueError):
            Limit.parse(bad)


def test_a_bucket_allows_a_burst_then_refills_steadily() -> None:
    bucket, limit = _Memory(), Limit(3, 3.0)  # 3 at once, then one a second
    taken = [bucket.take("k", limit, 1, now=100.0) for _ in range(4)]
    assert [d.allowed for d in taken] == [True, True, True, False]
    assert [d.remaining for d in taken[:3]] == [2, 1, 0]
    assert taken[3].retry_after_s == pytest.approx(1.0)
    assert not bucket.take("k", limit, 1, now=100.5).allowed
    assert bucket.take("k", limit, 1, now=101.0).allowed, "a second later, one more"
    assert bucket.take("other", limit, 1, now=101.0).allowed, "buckets are per subject"
    # Idle for long, it is full again, not fuller.
    assert [bucket.take("k", limit, 1, now=500.0).allowed for _ in range(4)] == [
        True,
        True,
        True,
        False,
    ]


# ── who the client is ────────────────────────────────────────


def _request(peer: str, xff: str | None = None) -> Any:
    headers = {"x-forwarded-for": xff} if xff else {}
    return SimpleNamespace(client=SimpleNamespace(host=peer), headers=headers)


@pytest.mark.parametrize(
    ("hops", "xff", "expected"),
    [
        (0, "6.6.6.6", "10.0.0.9"),  # no trusted proxy: the header is anyone's
        (1, "6.6.6.6, 203.0.113.7", "203.0.113.7"),  # what our proxy saw
        (2, "6.6.6.6, 203.0.113.7, 10.0.0.2", "203.0.113.7"),
        (1, None, "10.0.0.9"),
    ],
)
def test_forwarded_for_counts_only_behind_trusted_proxies(
    monkeypatch: pytest.MonkeyPatch, hops: int, xff: str | None, expected: str
) -> None:
    monkeypatch.setattr(settings, "trusted_proxy_hops", hops)
    assert api_rl.client_ip(_request("10.0.0.9", xff)) == expected


# ── in the API ───────────────────────────────────────────────


@pytest.fixture
def limits(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Limits on, fresh buckets; `limits(ip="3/60", ...)` sets some."""
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    ratelimit.memory.buckets.clear()

    def set_(**specs: str) -> None:
        for name, spec in specs.items():
            monkeypatch.setattr(settings, f"rate_limit_{name}", spec)

    return set_


async def test_every_request_is_limited_per_ip(client: AsyncClient, limits: Any) -> None:
    limits(ip="3/60")
    codes = [(await client.get("/api/v1/meta/graph-schema")).status_code for _ in range(3)]
    assert codes == [200, 200, 200]
    r = await client.get(
        "/api/v1/meta/graph-schema",
        headers={"X-Forwarded-For": "1.2.3.4", "Origin": "http://localhost:3000"},
    )
    assert r.status_code == 429, "a made-up X-Forwarded-For doesn't reset it"
    assert r.headers["retry-after"] == "20"
    # The web app is another origin: without CORS headers (and Retry-After
    # exposed) the browser would see an opaque failure.
    assert r.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "retry-after" in r.headers["access-control-expose-headers"].lower()
    body = r.json()
    assert body["error"]["code"] == "rate_limited"
    assert body["error"]["message"] == (
        "Too many requests from this address. Try again in 20 seconds."
    )
    assert body["error"]["details"] == {"retry_after_s": 20}
    assert r.headers["x-request-id"] == body["request_id"]
    assert (await client.get("/healthz")).status_code == 200, "probes are never limited"


async def test_sign_in_is_limited_per_address_and_per_account(
    client: AsyncClient, limits: Any
) -> None:
    await _register(client, "rl-login@example.com")
    limits(auth="100/60", login="2/300")
    wrong = {"email": "rl-login@example.com", "password": "not-the-password"}
    codes = [(await client.post("/api/v1/auth/login", json=wrong)).status_code for _ in range(3)]
    assert codes == [401, 401, 429], "guessing stops before the password is checked"
    right = {"email": "rl-login@example.com", "password": "supersecret"}
    assert (await client.post("/api/v1/auth/login", json=right)).status_code == 429
    other = {"email": "RL-Other@example.com", "password": "x" * 10}
    assert (await client.post("/api/v1/auth/login", json=other)).status_code == 401

    limits(auth="1/60")
    ratelimit.memory.buckets.clear()
    body = {"email": "rl-new@example.com", "password": "supersecret", "name": "N"}
    assert (await client.post("/api/v1/auth/register", json=body)).status_code == 201
    again = {**body, "email": "rl-new2@example.com"}
    r = await client.post("/api/v1/auth/register", json=again)
    assert r.status_code == 429
    assert r.json()["error"]["message"].startswith("Too many sign-in attempts from this address.")


async def test_each_user_has_their_own_allowance(client: AsyncClient, limits: Any) -> None:
    a = await _register(client, "rl-a@example.com")
    b = await _register(client, "rl-b@example.com")
    limits(user="2/60")
    me = [(await client.get("/api/v1/auth/me", headers=a.headers)).status_code for _ in range(3)]
    assert me == [200, 200, 429]
    assert (await client.get("/api/v1/auth/me", headers=b.headers)).status_code == 200


async def _conversation(client: AsyncClient, headers: dict[str, str], aid: str) -> str:
    r = await client.post(f"/api/v1/assistants/{aid}/conversations", json={}, headers=headers)
    return str(r.json()["id"])


async def _send(client: AsyncClient, headers: dict[str, str], cid: str) -> int:
    async with client.stream(
        "POST", f"/api/v1/conversations/{cid}/messages", json={"text": "hi"}, headers=headers
    ) as resp:
        await resp.aread()
        return resp.status_code


async def test_chat_is_limited_per_person_and_per_org(client: AsyncClient, limits: Any) -> None:
    owner = await _register(client, "rl-owner@example.com")
    member = await _register(client, "rl-member@example.com")
    org = (await client.post("/api/v1/orgs", json={"name": "RL"}, headers=owner.headers)).json()
    inv = await client.post(
        f"/api/v1/orgs/{org['id']}/invites",
        json={"email": "rl-member@example.com", "role": "member"},
        headers=owner.headers,
    )
    token = inv.json()["accept_url"].rsplit("/", 1)[-1]
    await client.post(f"/api/v1/invites/{token}/accept", headers=member.headers)
    o = {**owner.headers, "X-Org-Id": org["id"]}
    m = {**member.headers, "X-Org-Id": org["id"]}
    aid = (await client.post("/api/v1/assistants", json={"name": "RL"}, headers=o)).json()["id"]
    co, cm = await _conversation(client, o, aid), await _conversation(client, m, aid)

    limits(chat_user="1/60", chat_org="3/60")
    assert await _send(client, o, co) == 200
    assert await _send(client, o, co) == 429, "a real 429, before the stream opens"
    assert await _send(client, m, cm) == 200, "another person has their own"
    assert await _send(client, m, cm) == 429

    limits(chat_user="100/60", chat_org="3/60")
    ratelimit.memory.buckets.clear()
    sent = [await _send(client, who, c) for who, c in ((o, co), (m, cm), (o, co), (m, cm))]
    assert sent == [200, 200, 200, 429], "the org's allowance is shared"


async def test_the_ai_helpers_and_heavy_work_are_limited_per_user(
    client: AsyncClient, org_headers: dict[str, str], limits: Any
) -> None:
    aid = (
        await client.post("/api/v1/assistants", json={"name": "RL"}, headers=org_headers)
    ).json()["id"]
    limits(assist="1/60", heavy="1/60")
    body = {"description": "Answers questions about our returns policy."}
    url = f"/api/v1/assistants/{aid}/prompt:generate"
    assert (await client.post(url, json=body, headers=org_headers)).status_code == 200
    r = await client.post("/api/v1/pipeline:recommend", json=body, headers=org_headers)
    assert r.status_code == 429, "one allowance across the helpers"
    assert r.json()["error"]["message"].startswith("Too many requests to the AI helpers.")

    reindex = f"/api/v1/assistants/{aid}/data-sources/{uuid.uuid4()}:reindex"
    assert (await client.post(reindex, headers=org_headers)).status_code == 404
    assert (await client.post(reindex, headers=org_headers)).status_code == 429


async def test_limits_can_be_turned_off(client: AsyncClient, limits: Any) -> None:
    limits(ip="1/60")
    await client.get("/api/v1/meta/graph-schema")
    assert (await client.get("/api/v1/meta/graph-schema")).status_code == 429
    settings.rate_limit_enabled = False  # restored by the fixture's monkeypatch
    assert (await client.get("/api/v1/meta/graph-schema")).status_code == 200
    # ...and every per-route limit with it, not only the per-IP one.
    for _ in range(3):
        await api_rl.enforce("login", "someone", "1/60")


# ── Redis ────────────────────────────────────────────────────


async def test_without_redis_the_limits_still_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    """Down Redis must not lift every limit: the process's own buckets
    take over, and the outage is logged once, not per request."""

    class Down:
        async def eval(self, *_a: Any) -> Any:
            raise RedisConnectionError("down")

    warned: list[str] = []
    monkeypatch.setattr(ratelimit, "_use_redis", lambda: True)
    monkeypatch.setattr(ratelimit, "get_redis", Down)
    monkeypatch.setattr(ratelimit.log, "warning", lambda event, **_k: warned.append(event))
    monkeypatch.setitem(ratelimit._warned, "at", -1e9)
    ratelimit.memory.buckets.clear()
    got = [(await ratelimit.take("t", "s", Limit(2, 60))).allowed for _ in range(3)]
    assert got == [True, True, False]
    assert warned == ["rate_limit_redis_unavailable"]


@pytest.fixture
async def real_redis(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    get_redis.cache_clear()
    try:
        await get_redis().ping()
    except Exception:
        pytest.skip(f"Redis not reachable at {settings.redis_url}")
    monkeypatch.setattr(ratelimit, "_use_redis", lambda: True)
    yield
    get_redis.cache_clear()


@pytest.mark.integration
@pytest.mark.usefixtures("real_redis")
async def test_the_redis_bucket_is_shared_and_atomic() -> None:
    subject = f"test-{uuid.uuid4().hex}"
    limit = Limit(5, 50.0)  # 5 at once, then one per 10 s
    results = await asyncio.gather(*(ratelimit.take("t", subject, limit) for _ in range(20)))
    assert sum(d.allowed for d in results) == 5, "concurrent requests never share a token"
    denied = next(d for d in results if not d.allowed)
    assert 9.0 < denied.retry_after_s <= 10.0
    key = ratelimit._key("t", subject)
    assert subject not in key, "subjects are hashed, not stored in the clear"
    ttl = await get_redis().pttl(key)
    assert 0 < ttl <= 51_000, "the key expires once it would be full again"
    await get_redis().delete(key)
