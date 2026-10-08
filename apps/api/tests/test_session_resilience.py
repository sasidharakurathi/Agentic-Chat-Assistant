"""Staying signed in on a weak network, and logs that say who (Phase 7a.8).

On a phone, a token refresh can succeed on the server while its reply is
lost; the phone then tries again with the token it still holds. Reuse
detection read that as theft and revoked the whole session, and the client
read every failure (no network, 429, 5xx) as "signed out". Refreshes also
shared the per-IP sign-in bucket, so everyone behind one VPN address shared
it too.
"""

from __future__ import annotations

import logging
import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from app.config import settings
from app.security import ratelimit
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

pytestmark = pytest.mark.anyio

API = "/api/v1"


async def _signed_in(client: AsyncClient, email: str = "phone@example.com") -> str:
    r = await client.post(
        f"{API}/auth/register", json={"email": email, "password": "supersecret", "name": "P"}
    )
    assert r.status_code == 201, r.text
    return str(r.json()["refresh_token"])


async def _refresh(client: AsyncClient, token: str) -> Any:
    return await client.post(f"{API}/auth/refresh", json={"refresh_token": token})


# ── a reply lost on the way ──────────────────────────────────


async def test_a_retry_after_a_lost_reply_keeps_the_session(client: AsyncClient) -> None:
    first = await _signed_in(client)
    lost = await _refresh(client, first)  # the server rotated; the phone never heard
    assert lost.status_code == 200
    retry = await _refresh(client, first)
    assert retry.status_code == 200, retry.text
    fresh = retry.json()["refresh_token"]
    assert fresh not in (first, lost.json()["refresh_token"])
    # The session carries on from the retry's pair.
    assert (await _refresh(client, fresh)).status_code == 200


async def test_the_pair_from_the_lost_reply_is_retired(client: AsyncClient) -> None:
    """It never reached the phone; left live, it would be a second token in
    the same session."""
    first = await _signed_in(client)
    lost = (await _refresh(client, first)).json()["refresh_token"]
    retry = (await _refresh(client, first)).json()["refresh_token"]
    stale = await _refresh(client, lost)
    assert (stale.status_code, stale.json()["error"]["code"]) == (401, "refresh_reused")
    # Presenting a retired token is still treated as theft: the session ends.
    assert (await _refresh(client, retry)).status_code == 401


async def test_after_the_grace_window_reuse_still_ends_the_session(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "refresh_reuse_grace_s", 0)
    first = await _signed_in(client)
    second = (await _refresh(client, first)).json()["refresh_token"]
    reused = await _refresh(client, first)
    assert (reused.status_code, reused.json()["error"]["code"]) == (401, "refresh_reused")
    assert (await _refresh(client, second)).status_code == 401, "the whole session is revoked"


async def test_a_retry_older_than_the_window_is_reuse(client: AsyncClient) -> None:
    """The window is real: a token used a minute ago, with the grace at its
    usual 30 seconds, is reuse, not a lost reply."""
    from datetime import UTC, datetime, timedelta

    from app.db.session import get_sessionmaker
    from app.models.refresh_token import RefreshToken
    from app.security.tokens import decode_refresh_token
    from sqlalchemy import update

    assert settings.refresh_reuse_grace_s == 30
    first = await _signed_in(client)
    second = (await _refresh(client, first)).json()["refresh_token"]
    async with get_sessionmaker()() as s:
        await s.execute(
            update(RefreshToken)
            .where(RefreshToken.jti == decode_refresh_token(first).jti)
            .values(used_at=datetime.now(UTC) - timedelta(seconds=60))
        )
        await s.commit()
    reused = await _refresh(client, first)
    assert (reused.status_code, reused.json()["error"]["code"]) == (401, "refresh_reused")
    assert (await _refresh(client, second)).status_code == 401


async def test_a_session_that_moved_on_is_not_a_lost_reply(client: AsyncClient) -> None:
    """The pair from the first refresh was used, so it reached someone: an
    old token coming back now is reuse, inside the window or not."""
    first = await _signed_in(client)
    second = (await _refresh(client, first)).json()["refresh_token"]
    third = (await _refresh(client, second)).json()["refresh_token"]
    reused = await _refresh(client, first)
    assert (reused.status_code, reused.json()["error"]["code"]) == (401, "refresh_reused")
    assert (await _refresh(client, third)).status_code == 401


async def test_a_signed_out_session_gets_no_grace(client: AsyncClient) -> None:
    first = await _signed_in(client)
    second = (await _refresh(client, first)).json()["refresh_token"]
    await client.post(f"{API}/auth/logout", json={"refresh_token": second})
    assert (await _refresh(client, first)).status_code == 401
    assert (await _refresh(client, second)).status_code == 401


# ── limits per session, not per address ──────────────────────


@pytest.fixture
def limits(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    ratelimit.memory.buckets.clear()

    def set_(**specs: str) -> None:
        for name, spec in specs.items():
            monkeypatch.setattr(settings, f"rate_limit_{name}", spec)

    return set_


async def test_refreshing_is_limited_per_session_not_per_address(
    client: AsyncClient, limits: Any
) -> None:
    """Behind one VPN address, one busy session must not use up everyone
    else's refreshes, as the shared sign-in bucket used to."""
    limits(auth="2/60", refresh="2/60", refresh_ip="600/60")
    tokens = [await _signed_in(client, f"vpn{i}@example.com") for i in range(2)]
    ratelimit.memory.buckets.clear()
    busy = tokens[0]
    for _ in range(2):
        r = await _refresh(client, busy)
        assert r.status_code == 200, r.text
        busy = r.json()["refresh_token"]
    limited = await _refresh(client, busy)
    assert limited.status_code == 429
    assert "Retry-After" in limited.headers
    # Three refreshes from one address, more than the sign-in bucket allows:
    # the other session is unaffected.
    assert (await _refresh(client, tokens[1])).status_code == 200


async def test_a_refresh_no_longer_spends_the_sign_in_bucket(
    client: AsyncClient, limits: Any
) -> None:
    token = await _signed_in(client)
    limits(auth="1/60", refresh="100/60")
    ratelimit.memory.buckets.clear()
    for _ in range(3):
        r = await _refresh(client, token)
        assert r.status_code == 200, r.text
        token = r.json()["refresh_token"]


# ── who, and from where ──────────────────────────────────────


async def test_the_access_log_names_the_user_and_the_address(
    client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    r = await client.post(
        f"{API}/auth/register",
        json={"email": "who@example.com", "password": "supersecret", "name": "W"},
    )
    access = r.json()["access_token"]
    me = (await client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {access}"})).json()
    with caplog.at_level(logging.INFO, logger="api.access"):
        await client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {access}"})
        await client.get(f"{API}/meta/samples")
    lines = [r.getMessage() for r in caplog.records if r.name == "api.access"]
    signed_in, anonymous = lines[-2], lines[-1]
    assert me["user"]["id"] in signed_in
    assert "client_ip" in signed_in and "client_ip" in anonymous
    assert "user_id" in anonymous and me["user"]["id"] not in anonymous


# ── the audit log is append-only (Postgres) ──────────────────

POSTGRES_URL = os.environ.get(
    "INTEGRATION_DATABASE_URL", "postgresql+asyncpg://app:app@localhost:45432/app"
)


@pytest.fixture
async def pg() -> AsyncIterator[AsyncConnection]:
    """One transaction on the dev database, rolled back at the end: the rows
    it inserts can't be deleted afterwards, so they are never committed."""
    engine = create_async_engine(POSTGRES_URL)
    try:
        async with engine.connect() as conn:
            found = await conn.scalar(
                text("SELECT 1 FROM pg_trigger WHERE tgname = 'audit_log_append_only'")
            )
    except Exception as exc:  # pragma: no cover - environment-dependent
        await engine.dispose()
        pytest.skip(f"Postgres not reachable at {POSTGRES_URL}: {exc}")
    if not found:
        await engine.dispose()
        pytest.fail("the audit_log trigger is missing: run `alembic upgrade head`")
    async with engine.connect() as conn:
        tx = await conn.begin()
        try:
            yield conn
        finally:
            await tx.rollback()
    await engine.dispose()


@pytest.mark.integration
async def test_an_audit_row_cannot_be_changed_or_deleted(pg: AsyncConnection) -> None:
    org = uuid.uuid4()
    await pg.execute(
        text(
            "INSERT INTO organizations (id, name, slug, is_personal) VALUES (:id, 'A', :s, false)"
        ),
        {"id": org, "s": f"append-only-{org.hex[:8]}"},
    )
    row = uuid.uuid4()
    await pg.execute(
        text(
            "INSERT INTO audit_log (id, org_id, action, meta, created_at) "
            "VALUES (:id, :org, 'member.role_change', '{}', now())"
        ),
        {"id": row, "org": org},
    )
    for statement, says in (
        ("UPDATE audit_log SET action = 'nothing.happened' WHERE id = :id", "cannot be changed"),
        ("UPDATE audit_log SET meta = '{\"edited\": true}' WHERE id = :id", "cannot be changed"),
        ("UPDATE audit_log SET ip = '10.0.0.1' WHERE id = :id", "cannot be changed"),
        ("DELETE FROM audit_log WHERE id = :id", "cannot be deleted"),
    ):
        savepoint = await pg.begin_nested()
        with pytest.raises(DBAPIError, match=says):
            await pg.execute(text(statement), {"id": row})
        await savepoint.rollback()

    # The one change allowed: the org it names is deleted, and the foreign
    # key blanks the reference. The record itself stays.
    await pg.execute(text("DELETE FROM organizations WHERE id = :id"), {"id": org})
    left = (
        await pg.execute(text("SELECT org_id, action FROM audit_log WHERE id = :id"), {"id": row})
    ).one()
    assert (left.org_id, left.action) == (None, "member.role_change")
