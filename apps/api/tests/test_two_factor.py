"""Two-step sign-in (Phase 8.4).

An authenticator app's codes (RFC 6238, checked against the RFC's own test
vectors) and ten single-use recovery codes. A password sign-in to an
account that has it ends in a challenge, finished with a code; a code works
once. With REQUIRE_ADMIN_2FA, an owner or admin acts as an admin only in a
session that passed a second factor, and as a builder until then. The
secret is sealed with the KEK and moves with a key rotation. Limits are by
person; an office or VPN range gets a higher per-address ceiling.
"""

from __future__ import annotations

import base64
import os
import uuid
from typing import Any

import pytest
from app import preflight
from app.api import ratelimit
from app.config import settings
from app.db.session import get_sessionmaker
from app.models.audit_log import AuditLog
from app.models.second_factor import SEALED_KIND, UserSecondFactor
from app.models.user import User
from app.security import totp
from app.security.crypto import SealedSecret, open_sealed
from app.security.rotation import rotate_kek
from app.services import two_factor
from httpx import AsyncClient
from sqlalchemy import select
from tests.test_deploy import _production

from test_orgs import _register  # type: ignore[import-not-found]

pytestmark = pytest.mark.anyio

API = "/api/v1"
PASSWORD = "supersecret"


# ── the codes themselves ─────────────────────────────────────


@pytest.mark.parametrize(
    ("at", "code"),
    [
        (59, "94287082"),
        (1111111109, "07081804"),
        (1111111111, "14050471"),
        (1234567890, "89005924"),
        (2000000000, "69279037"),
        (20000000000, "65353130"),
    ],
)
def test_codes_match_the_rfcs_test_vectors(at: int, code: str) -> None:
    """RFC 6238 Appendix B, HMAC-SHA1, eight digits."""
    assert totp.code_at(b"12345678901234567890", at // totp.STEP_S, 8) == code


def test_a_code_is_good_for_its_step_and_the_ones_beside_it_once() -> None:
    secret = totp.new_secret()
    now = 1_700_000_000.0
    step = totp.step_of(now)
    assert totp.verify(secret, totp.code_at(secret, step), at=now) == step
    assert totp.verify(secret, totp.code_at(secret, step - 1), at=now) == step - 1
    assert totp.verify(secret, totp.code_at(secret, step - 2), at=now) is None
    assert totp.verify(secret, totp.code_at(secret, step), after_step=step, at=now) is None
    assert totp.verify(secret, "12 34", at=now) is None


# ── setting it up, and signing in with it ────────────────────


async def _secret(user_id: str) -> str:
    async with get_sessionmaker()() as s:
        row = await s.scalar(
            select(UserSecondFactor).where(UserSecondFactor.user_id == uuid.UUID(user_id))
        )
        assert row is not None
        sealed = SealedSecret(
            ciphertext=row.ciphertext, dek_wrapped=row.dek_wrapped, nonce=row.nonce
        )
        return open_sealed(sealed, kind=SEALED_KIND)


def _code(secret: str, ahead: int = 0) -> str:
    """A code for now, or for a step ahead: each code works once, and the
    next one is good a step early."""
    return totp.code_at(secret, totp.step_of() + ahead)


async def _turn_on(client: AsyncClient, h: dict[str, str], user_id: str) -> dict[str, Any]:
    started = await client.post(f"{API}/auth/two-factor/setup", headers=h)
    assert started.status_code == 200, started.text
    assert started.json()["otpauth_uri"].startswith("otpauth://totp/")
    secret = await _secret(user_id)
    assert secret == started.json()["secret"]
    done = await client.post(
        f"{API}/auth/two-factor/confirm", json={"code": _code(secret)}, headers=h
    )
    assert done.status_code == 200, done.text
    return {"secret": secret, **done.json()}


async def test_turning_it_on_gives_codes_and_a_verified_session(client: AsyncClient) -> None:
    me = await _register(client, "pat@example.com")
    on = await _turn_on(client, me.headers, me.user_id)
    assert len(on["recovery_codes"]) == 10 and len(set(on["recovery_codes"])) == 10
    fresh = {"Authorization": f"Bearer {on['tokens']['access_token']}"}
    status = (await client.get(f"{API}/auth/me", headers=fresh)).json()["two_factor"]
    assert (status["enabled"], status["session_verified"], status["recovery_codes_left"]) == (
        True,
        True,
        10,
    )
    old = (await client.get(f"{API}/auth/two-factor", headers=me.headers)).json()
    assert old["session_verified"] is False, "the session from before stays as it was"


async def test_a_password_alone_no_longer_signs_in(client: AsyncClient) -> None:
    me = await _register(client, "pat@example.com")
    on = await _turn_on(client, me.headers, me.user_id)
    r = await client.post(
        f"{API}/auth/login", json={"email": "pat@example.com", "password": PASSWORD}
    )
    body = r.json()
    assert body["access_token"] is None and body["two_factor_challenge"]
    url = f"{API}/auth/login/two-factor"
    wrong = await client.post(
        url, json={"challenge": body["two_factor_challenge"], "code": "000000"}
    )
    assert (wrong.status_code, wrong.json()["error"]["code"]) == (401, "two_factor_code")
    good = _code(on["secret"], ahead=1)
    ok = await client.post(url, json={"challenge": body["two_factor_challenge"], "code": good})
    assert ok.status_code == 200, ok.text
    h = {"Authorization": f"Bearer {ok.json()['access_token']}"}
    assert (await client.get(f"{API}/auth/me", headers=h)).json()["two_factor"]["session_verified"]
    # The same code, again: refused.
    again = await client.post(url, json={"challenge": body["two_factor_challenge"], "code": good})
    assert again.status_code == 401

    async with get_sessionmaker()() as s:
        failed = list(
            await s.scalars(
                select(AuditLog.meta)
                .where(AuditLog.action == "user.login_failed")
                .execution_options(all_orgs=True)
            )
        )
    assert {"reason": "two_factor"} in failed


async def test_a_recovery_code_works_once(client: AsyncClient) -> None:
    me = await _register(client, "pat@example.com")
    on = await _turn_on(client, me.headers, me.user_id)
    code = on["recovery_codes"][0].upper().replace("-", " ")
    challenge = (
        await client.post(
            f"{API}/auth/login", json={"email": "pat@example.com", "password": PASSWORD}
        )
    ).json()["two_factor_challenge"]
    url = f"{API}/auth/login/two-factor"
    assert (await client.post(url, json={"challenge": challenge, "code": code})).status_code == 200
    assert (await client.post(url, json={"challenge": challenge, "code": code})).status_code == 401


async def test_two_requests_with_one_code_cannot_both_pass(client: AsyncClient) -> None:
    """Both requests read the factor before either claims the code: the
    claim itself (a conditional update) lets only the first through."""
    me = await _register(client, "pat@example.com")
    on = await _turn_on(client, me.headers, me.user_id)
    uid = uuid.UUID(me.user_id)
    code = _code(on["secret"], ahead=1)
    async with get_sessionmaker()() as first, get_sessionmaker()() as second:
        user = await first.get(User, uid)
        assert user is not None
        # The second request has read the factor already, as it was (held,
        # so the session keeps that copy rather than reading it again).
        stale = await two_factor._factor(second, uid)
        assert stale is not None
        await second.commit()
        assert await two_factor.check(first, user, code) == "totp"
        await first.commit()
        assert await two_factor.check(second, user, code) is None
        assert stale.last_step < totp.step_of() + 1, "it really read the old value"


async def test_a_challenge_can_be_neither_forged_nor_kept(client: AsyncClient) -> None:
    r = await client.post(
        f"{API}/auth/login/two-factor", json={"challenge": "x" * 40, "code": "123456"}
    )
    assert (r.status_code, r.json()["error"]["code"]) == (401, "challenge_expired")


async def test_turning_it_off_or_changing_codes_needs_a_code(client: AsyncClient) -> None:
    me = await _register(client, "pat@example.com")
    on = await _turn_on(client, me.headers, me.user_id)
    h = {"Authorization": f"Bearer {on['tokens']['access_token']}"}
    bad = await client.post(f"{API}/auth/two-factor/disable", json={"code": "000000"}, headers=h)
    assert bad.status_code == 400
    fresh = await client.post(
        f"{API}/auth/two-factor/recovery-codes", json={"code": _code(on["secret"], 1)}, headers=h
    )
    assert fresh.status_code == 200
    assert set(fresh.json()["recovery_codes"]).isdisjoint(on["recovery_codes"])
    off = await client.post(
        f"{API}/auth/two-factor/disable",
        json={"code": fresh.json()["recovery_codes"][0]},
        headers=h,
    )
    assert off.status_code == 200
    login = await client.post(
        f"{API}/auth/login", json={"email": "pat@example.com", "password": PASSWORD}
    )
    assert login.json()["access_token"], "a password signs in again"


async def test_the_operator_can_turn_it_off_for_someone_locked_out(client: AsyncClient) -> None:
    me = await _register(client, "pat@example.com")
    await _turn_on(client, me.headers, me.user_id)
    async with get_sessionmaker()() as s:
        user = await s.get(User, uuid.UUID(me.user_id))
        assert user is not None
        await two_factor.reset(s, user)
        assert not await two_factor.enabled(s, user.id)
        actions = list(
            await s.scalars(
                select(AuditLog.action)
                .where(AuditLog.action == "user.two_factor.reset")
                .execution_options(all_orgs=True)
            )
        )
    assert actions == ["user.two_factor.reset"]


# ── admins ───────────────────────────────────────────────────


async def test_without_it_an_admin_acts_as_a_builder(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "require_admin_2fa", True)
    owner = await _register(client, "owner@example.com")
    org = (await client.post(f"{API}/orgs", json={"name": "Acme"}, headers=owner.headers)).json()
    url = f"{API}/orgs/{org['id']}/settings"
    held = await client.get(url, headers=owner.headers)
    assert (held.status_code, held.json()["error"]["code"]) == (403, "insufficient_role")
    status = (await client.get(f"{API}/auth/two-factor", headers=owner.headers)).json()
    assert (status["required"], status["session_verified"]) == (True, False)
    # Building still works: they are a builder until then.
    built = await client.post(
        f"{API}/assistants", json={"name": "Desk"}, headers={**owner.headers, "X-Org-Id": org["id"]}
    )
    assert built.status_code == 201

    on = await _turn_on(client, owner.headers, owner.user_id)
    h = {"Authorization": f"Bearer {on['tokens']['access_token']}"}
    assert (await client.get(url, headers=h)).status_code == 200
    # A session from before is opened with a code, without signing in again.
    assert (await client.get(url, headers=owner.headers)).status_code == 403
    verified = await client.post(
        f"{API}/auth/two-factor/verify",
        json={"code": _code(on["secret"], 1)},
        headers=owner.headers,
    )
    assert verified.status_code == 200
    h2 = {"Authorization": f"Bearer {verified.json()['access_token']}"}
    assert (await client.get(url, headers=h2)).status_code == 200


async def test_a_renewed_session_keeps_its_second_factor(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "require_admin_2fa", True)
    owner = await _register(client, "owner@example.com")
    org = (await client.post(f"{API}/orgs", json={"name": "Acme"}, headers=owner.headers)).json()
    on = await _turn_on(client, owner.headers, owner.user_id)
    renewed = await client.post(
        f"{API}/auth/refresh", json={"refresh_token": on["tokens"]["refresh_token"]}
    )
    assert renewed.status_code == 200
    h = {"Authorization": f"Bearer {renewed.json()['access_token']}"}
    assert (await client.get(f"{API}/orgs/{org['id']}/settings", headers=h)).status_code == 200


# ── the secret, sealed ───────────────────────────────────────


async def test_a_key_rotation_moves_the_authenticator_secrets_too(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    me = await _register(client, "pat@example.com")
    on = await _turn_on(client, me.headers, me.user_id)
    old, new = settings.app_kek, base64.b64encode(os.urandom(32)).decode()
    async with get_sessionmaker()() as s:
        report = await rotate_kek(s, old_kek=old, new_kek=new)
    assert report.rotated >= 1
    monkeypatch.setattr(settings, "app_kek", new)
    assert await _secret(me.user_id) == on["secret"]


# ── limits by person ─────────────────────────────────────────


def test_office_and_vpn_ranges_get_a_higher_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "trusted_internal_nets", "10.0.0.0/8, 100.64.0.0/10")
    assert ratelimit.is_internal("10.1.2.3") and ratelimit.is_internal("100.101.1.1")
    assert not ratelimit.is_internal("203.0.113.9") and not ratelimit.is_internal(None)
    assert ratelimit.per_address("10.1.2.3", "20/60", "600/60") == "600/60"
    assert ratelimit.per_address("203.0.113.9", "20/60", "600/60") == "20/60"


def test_the_preflight_wants_it_in_production() -> None:
    def findings(**over: Any) -> dict[str, str]:
        report = preflight.Report()
        preflight.review_two_factor(_production(**over), report)
        return {f.name: f.level for f in report.findings}

    assert findings(require_admin_2fa=True) == {}
    assert findings(require_admin_2fa=False) == {"REQUIRE_ADMIN_2FA": "warn"}
    assert findings(require_admin_2fa=True, trusted_internal_nets="not-a-range") == {
        "TRUSTED_INTERNAL_NETS": "fail"
    }
