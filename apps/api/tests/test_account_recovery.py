"""Getting back in, changing a password, confirming an address (Phase 7b.6).

A reset is asked for by address and answered the same either way; its link
works once, for a while, and a newer one voids it. Using it, or changing the
password while signed in, ends every session (refresh tokens revoked, older
access tokens refused) and emails a notice. An address is confirmed by an
emailed link, or by joining through an invite to it. Links carry their
token after `#`. An operator can print a reset link when email can't help.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from app.config import settings
from app.db.session import get_sessionmaker
from app.mail import backends
from app.models.audit_log import AuditLog
from app.models.user import User
from app.models.user_token import TokenPurpose, UserToken
from httpx import AsyncClient
from sqlalchemy import select, update

from test_orgs import _register  # type: ignore[import-not-found]

pytestmark = pytest.mark.anyio

API = "/api/v1"
PASSWORD = "supersecret"


@pytest.fixture(autouse=True)
def _empty_outbox() -> Any:
    backends.outbox.clear()
    yield
    backends.outbox.clear()


def _link(kind: str) -> str:
    (m,) = [x for x in backends.outbox if x.kind == kind]
    found = re.search(r"https?://\S+", m.text)
    assert found is not None
    return found.group(0)


def _token(kind: str) -> str:
    url = _link(kind)
    assert "#token=" in url, "the token travels after #, never to a server"
    return url.split("#token=", 1)[1]


BASE = settings.app_base_url.rstrip("/")


async def _confirm(client: AsyncClient, token: str, password: str) -> Any:
    return await client.post(
        f"{API}/auth/password-reset/confirm", json={"token": token, "new_password": password}
    )


async def _login(client: AsyncClient, email: str, password: str) -> Any:
    return await client.post(f"{API}/auth/login", json={"email": email, "password": password})


# ── reset ────────────────────────────────────────────────────


async def test_the_reset_form_tells_nobody_who_has_an_account(client: AsyncClient) -> None:
    await _register(client, "pat@example.com")
    known = await client.post(f"{API}/auth/password-reset", json={"email": "pat@example.com"})
    unknown = await client.post(f"{API}/auth/password-reset", json={"email": "nobody@example.com"})
    assert (known.status_code, unknown.status_code) == (202, 202)
    assert known.json() == unknown.json()
    assert [m.to for m in backends.outbox] == ["pat@example.com"]


async def test_a_reset_link_sets_the_password_once_and_ends_every_session(
    client: AsyncClient,
) -> None:
    actor = await _register(client, "pat@example.com")
    old_refresh = (await _login(client, "pat@example.com", PASSWORD)).json()["refresh_token"]
    await client.post(f"{API}/auth/password-reset", json={"email": "pat@example.com"})
    token = _token("password_reset")
    assert _link("password_reset").startswith(
        f"{settings.app_base_url.rstrip('/')}/reset-password#"
    )

    r = await client.post(
        f"{API}/auth/password-reset/confirm",
        json={"token": token, "new_password": "brand-new-pass"},
    )
    assert r.status_code == 200, r.text
    again = await _confirm(client, token, "other-pass-1")
    assert (again.status_code, again.json()["error"]["code"]) == (400, "link_expired")

    assert (await _login(client, "pat@example.com", PASSWORD)).status_code == 401
    assert (await _login(client, "pat@example.com", "brand-new-pass")).status_code == 200
    renewed = await client.post(f"{API}/auth/refresh", json={"refresh_token": old_refresh})
    assert renewed.status_code == 401, "every session ended"
    me = await client.get(f"{API}/auth/me", headers=actor.headers)
    assert (me.status_code, me.json()["error"]["code"]) == (401, "session_ended")
    assert [m.kind for m in backends.outbox if m.kind == "password_changed"] == ["password_changed"]


async def test_a_newer_link_voids_the_older_and_links_expire(client: AsyncClient) -> None:
    await _register(client, "pat@example.com")
    await client.post(f"{API}/auth/password-reset", json={"email": "pat@example.com"})
    first = _token("password_reset")
    backends.outbox.clear()
    await client.post(f"{API}/auth/password-reset", json={"email": "pat@example.com"})
    second = _token("password_reset")
    r = await client.post(
        f"{API}/auth/password-reset/confirm",
        json={"token": first, "new_password": "brand-new-pass"},
    )
    assert r.status_code == 400, "replaced by the newer link"

    async with get_sessionmaker()() as s:
        await s.execute(
            update(UserToken).values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
        )
        await s.commit()
    r = await client.post(
        f"{API}/auth/password-reset/confirm",
        json={"token": second, "new_password": "brand-new-pass"},
    )
    assert (r.status_code, r.json()["error"]["code"]) == (400, "link_expired")


async def test_a_switched_off_account_gets_no_reset(client: AsyncClient) -> None:
    await _register(client, "gone@example.com")
    async with get_sessionmaker()() as s:
        await s.execute(update(User).values(is_active=False))
        await s.commit()
    r = await client.post(f"{API}/auth/password-reset", json={"email": "gone@example.com"})
    assert r.status_code == 202 and backends.outbox == []


async def test_tokens_are_stored_hashed(client: AsyncClient) -> None:
    await _register(client, "pat@example.com")
    await client.post(f"{API}/auth/password-reset", json={"email": "pat@example.com"})
    raw = _token("password_reset")
    async with get_sessionmaker()() as s:
        (row,) = (await s.scalars(select(UserToken))).all()
    assert row.token_hash != raw and len(row.token_hash) == 64
    assert row.purpose is TokenPurpose.password_reset


# ── change ───────────────────────────────────────────────────


async def test_changing_the_password_keeps_this_session_and_ends_the_others(
    client: AsyncClient,
) -> None:
    actor = await _register(client, "pat@example.com")
    other = (await _login(client, "pat@example.com", PASSWORD)).json()["refresh_token"]
    wrong = await client.post(
        f"{API}/auth/password",
        json={"current_password": "not-it", "new_password": "brand-new-pass"},
        headers=actor.headers,
    )
    assert (wrong.status_code, wrong.json()["error"]["code"]) == (401, "wrong_password")
    r = await client.post(
        f"{API}/auth/password",
        json={"current_password": PASSWORD, "new_password": "brand-new-pass"},
        headers=actor.headers,
    )
    assert r.status_code == 200, r.text
    fresh = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert (await client.get(f"{API}/auth/me", headers=fresh)).status_code == 200
    renewed = await client.post(f"{API}/auth/refresh", json={"refresh_token": other})
    assert renewed.status_code == 401
    async with get_sessionmaker()() as s:
        action = await s.scalar(
            select(AuditLog.action).where(AuditLog.action == "user.password_change")
        )
    assert action == "user.password_change"


# ── verification ─────────────────────────────────────────────


async def test_an_address_is_confirmed_by_its_link(client: AsyncClient) -> None:
    actor = await _register(client, "pat@example.com")
    me = (await client.get(f"{API}/auth/me", headers=actor.headers)).json()
    assert me["user"]["email_verified_at"] is None
    r = await client.post(f"{API}/auth/verify-email/send", headers=actor.headers)
    assert r.status_code == 200
    token = _token("verify_email")
    assert _link("verify_email").startswith(f"{BASE}/verify-email#")
    r = await client.post(f"{API}/auth/verify-email", json={"token": token})
    assert r.status_code == 200, r.text
    me = (await client.get(f"{API}/auth/me", headers=actor.headers)).json()
    assert me["user"]["email_verified_at"] is not None
    again = await client.post(f"{API}/auth/verify-email/send", headers=actor.headers)
    assert again.json()["message"] == "Your address is already confirmed."


async def test_joining_through_an_invite_confirms_the_address(client: AsyncClient) -> None:
    owner = await _register(client, "owner@example.com")
    org = (await client.post(f"{API}/orgs", json={"name": "Team"}, headers=owner.headers)).json()
    inv = await client.post(
        f"{API}/orgs/{org['id']}/invites",
        json={"email": "new@example.com", "role": "member"},
        headers=owner.headers,
    )
    token = inv.json()["accept_url"].rsplit("/", 1)[-1]
    r = await client.post(
        f"{API}/auth/register",
        json={"email": "new@example.com", "password": PASSWORD, "name": "N", "invite_token": token},
    )
    me = (
        await client.get(
            f"{API}/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"}
        )
    ).json()
    assert me["user"]["email_verified_at"] is not None


# ── the operator's way in ────────────────────────────────────


async def test_an_operator_can_print_a_reset_link(
    client: AsyncClient, capsys: pytest.CaptureFixture[str]
) -> None:
    from scripts import reset_link

    await _register(client, "owner@example.com")
    assert await reset_link._main("owner@example.com") == 0
    printed = capsys.readouterr().out.strip()
    assert "/reset-password#token=" in printed
    token = printed.split("#token=", 1)[1]
    r = await client.post(
        f"{API}/auth/password-reset/confirm",
        json={"token": token, "new_password": "brand-new-pass"},
    )
    assert r.status_code == 200, r.text
    assert await reset_link._main("nobody@example.com") == 1


async def test_a_switched_off_account_cannot_use_its_reset_link(client: AsyncClient) -> None:
    await _register(client, "pat@example.com")
    await client.post(f"{API}/auth/password-reset", json={"email": "pat@example.com"})
    token = _token("password_reset")
    async with get_sessionmaker()() as s:
        await s.execute(update(User).where(User.email == "pat@example.com").values(is_active=False))
        await s.commit()
    r = await _confirm(client, token, "brand-new-pass")
    assert (r.status_code, r.json()["error"]["code"]) == (400, "link_expired")
