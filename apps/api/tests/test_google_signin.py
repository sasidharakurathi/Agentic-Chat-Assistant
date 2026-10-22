"""Google sign-in (Phase 8.3).

The account is found by Google's id for the person, never by address. A
Google sign-in whose address already has a password account is refused;
that person connects Google from their account, signed in. New accounts
come only through an invite to that address, or the owner's opt-in
auto-join for the company's Google Workspace domain, as Employees, with
no password. The flow is bound to the browser that started it (state,
nonce, PKCE), Google's token is checked in full, and the session tokens
reach the page through a one-minute code, never an address.

Google itself is stood in for: `exchange` and the key lookup are replaced,
and the token checks run on tokens signed here with a real RSA key.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import jwt
import pytest
from app import preflight
from app.config import settings
from app.db.session import get_sessionmaker
from app.mail import backends
from app.models.audit_log import AuditLog
from app.models.user import User
from app.services import google
from app.services.google import GoogleIdentity, GoogleRefused
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import AsyncClient, Response
from sqlalchemy import select
from tests.test_deploy import _production

from test_orgs import _register  # type: ignore[import-not-found]

pytestmark = pytest.mark.anyio

API = "/api/v1"
CLIENT_ID = "app.apps.googleusercontent.com"
DOMAIN = "company.example"


@pytest.fixture(autouse=True)
def _google_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "google_client_id", CLIENT_ID)
    monkeypatch.setattr(settings, "google_client_secret", "a-secret")
    monkeypatch.setattr(settings, "google_allowed_domain", DOMAIN)
    backends.outbox.clear()


def _who(email: str = "pat@company.example", sub: str = "g-111", **over: Any) -> GoogleIdentity:
    fields: dict[str, Any] = {
        "subject": sub,
        "email": email,
        "email_verified": True,
        "domain": DOMAIN,
        "name": "Pat",
    }
    return GoogleIdentity(**{**fields, **over})


async def _flow(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    who: GoogleIdentity,
    **params: str,
) -> Response:
    """Start a sign-in, let "Google" answer for `who`, return the callback's
    redirect."""
    started = await client.get(f"{API}/auth/google/start", params=params)
    assert started.status_code == 302, started.text
    state = parse_qs(urlsplit(started.headers["location"]).query)["state"][0]

    async def exchange(_code: str, _verifier: str) -> str:
        return "an-id-token"

    monkeypatch.setattr(google, "exchange", exchange)
    monkeypatch.setattr(google, "verify", lambda _t, _n: who)
    return await client.get(f"{API}/auth/google/callback", params={"code": "c", "state": state})


def _outcome(r: Response) -> str:
    """Where the callback sent the browser: an error code, or `signed-in`."""
    assert r.status_code == 302, r.text
    where = r.headers["location"]
    if "/auth/google#code=" in where:
        return "signed-in"
    # Exactly: "linked" is also the start of "linked_elsewhere".
    return parse_qs(urlsplit(where).query)["google"][0]


async def _tokens(client: AsyncClient, r: Response) -> dict[str, Any]:
    code = r.headers["location"].split("#code=", 1)[1].split("&", 1)[0]
    out = await client.post(f"{API}/auth/google/exchange", json={"code": code})
    assert out.status_code == 200, out.text
    return {"code": code, **out.json()}


async def _invite(client: AsyncClient, email: str, role: str = "employee") -> tuple[str, str]:
    owner = await _register(client, "owner@example.com")
    org = (await client.post(f"{API}/orgs", json={"name": "Acme"}, headers=owner.headers)).json()
    made = await client.post(
        f"{API}/orgs/{org['id']}/invites",
        json={"email": email, "role": role},
        headers=owner.headers,
    )
    assert made.status_code == 201, made.text
    return org["id"], made.json()["accept_url"].rsplit("/", 1)[-1]


async def _actions(action: str) -> list[dict[str, Any]]:
    async with get_sessionmaker()() as s:
        return [
            dict(m)
            for m in await s.scalars(
                select(AuditLog.meta)
                .where(AuditLog.action == action)
                .execution_options(all_orgs=True)
            )
        ]


# ── the flow ─────────────────────────────────────────────────


async def test_the_sign_in_page_learns_what_is_on(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    on = (await client.get(f"{API}/auth/providers")).json()
    assert (on["google"], on["google_domain"]) == (True, DOMAIN)
    monkeypatch.setattr(settings, "google_client_id", "")
    assert (await client.get(f"{API}/auth/providers")).json()["google"] is False
    assert (await client.get(f"{API}/auth/google/start")).status_code == 404


async def test_the_start_binds_the_flow_to_this_browser(client: AsyncClient) -> None:
    r = await client.get(f"{API}/auth/google/start")
    query = parse_qs(urlsplit(r.headers["location"]).query)
    assert query["client_id"] == [CLIENT_ID]
    assert query["code_challenge_method"] == ["S256"] and query["code_challenge"][0]
    assert query["nonce"][0] and query["state"][0]
    assert query["hd"] == [DOMAIN], "a hint to Google's picker; the claim is what is checked"
    cookie = r.headers["set-cookie"]
    assert "httponly" in cookie.lower() and "path=/api/v1/auth/google" in cookie.lower()


async def test_a_callback_this_browser_didnt_start_is_refused(client: AsyncClient) -> None:
    await client.get(f"{API}/auth/google/start")
    r = await client.get(f"{API}/auth/google/callback", params={"code": "c", "state": "forged"})
    assert _outcome(r) == "state"
    client.cookies.clear()
    r = await client.get(f"{API}/auth/google/callback", params={"code": "c", "state": "x"})
    assert _outcome(r) == "state"


# ── who may come in ──────────────────────────────────────────


async def test_without_an_invite_nobody_new_comes_in(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _outcome(await _flow(client, monkeypatch, _who())) == "no_invite"


async def test_an_invite_makes_a_google_only_account(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    org_id, token = await _invite(client, "pat@company.example")
    r = await _flow(client, monkeypatch, _who(), invite=token)
    assert _outcome(r) == "signed-in"
    got = await _tokens(client, r)
    h = {"Authorization": f"Bearer {got['access_token']}"}
    me = (await client.get(f"{API}/auth/me", headers=h)).json()
    assert [(m["org_id"], m["role"]) for m in me["memberships"]] == [(org_id, "employee")]
    assert me["sign_in"] == {"password": False, "google": "pat@company.example"}
    assert me["user"]["email_verified_at"] is not None

    # No password: a password sign-in opens nothing.
    login = await client.post(
        f"{API}/auth/login", json={"email": "pat@company.example", "password": "anything-at-all"}
    )
    assert login.status_code == 401
    # The code works once.
    again = await client.post(f"{API}/auth/google/exchange", json={"code": got["code"]})
    assert again.status_code == 400


async def test_an_invite_for_another_address_opens_nothing(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, token = await _invite(client, "someone-else@company.example")
    assert _outcome(await _flow(client, monkeypatch, _who(), invite=token)) == "invite"


async def test_coming_back_opens_the_same_account(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, token = await _invite(client, "pat@company.example")
    first = await _tokens(client, await _flow(client, monkeypatch, _who(), invite=token))
    # Google says the address changed; the id is what counts.
    r = await _flow(client, monkeypatch, _who(email="pat.new@company.example"))
    second = await _tokens(client, r)
    me1 = (
        await client.get(
            f"{API}/auth/me", headers={"Authorization": f"Bearer {first['access_token']}"}
        )
    ).json()
    me2 = (
        await client.get(
            f"{API}/auth/me", headers={"Authorization": f"Bearer {second['access_token']}"}
        )
    ).json()
    assert me1["user"]["id"] == me2["user"]["id"]
    assert {"via": "google"} in await _actions("user.login")


async def test_an_address_with_a_password_account_is_never_taken_over(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The classic: someone else's Google account with the same address, or
    a recycled mailbox. No link by address, invite or not."""
    await _register(client, "pat@company.example")
    assert _outcome(await _flow(client, monkeypatch, _who())) == "password_account"
    _, token = await _invite(client, "pat@company.example")
    assert _outcome(await _flow(client, monkeypatch, _who(), invite=token)) == "password_account"
    assert await _actions("user.identity.link") == []


async def test_a_switched_off_account_is_refused(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, token = await _invite(client, "pat@company.example")
    await _flow(client, monkeypatch, _who(), invite=token)
    async with get_sessionmaker()() as s:
        user = await s.scalar(select(User).where(User.email == "pat@company.example"))
        assert user is not None
        user.is_active = False
        await s.commit()
    assert _outcome(await _flow(client, monkeypatch, _who())) == "account_disabled"


# ── connecting Google to an account, signed in ───────────────


async def _link(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, h: dict[str, str], who: GoogleIdentity
) -> str:
    ticket = (await client.post(f"{API}/auth/google/link-ticket", headers=h)).json()["ticket"]
    return _outcome(await _flow(client, monkeypatch, who, ticket=ticket))


async def test_a_password_account_connects_google_from_its_account(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    me = await _register(client, "pat@company.example")
    assert await _link(client, monkeypatch, me.headers, _who()) == "linked"
    shown = (await client.get(f"{API}/auth/me", headers=me.headers)).json()["sign_in"]
    assert shown == {"password": True, "google": "pat@company.example"}
    assert len(await _actions("user.identity.link")) == 1
    # Now Google opens it.
    r = await _flow(client, monkeypatch, _who())
    assert _outcome(r) == "signed-in"

    other = await _register(client, "sam@company.example")
    assert await _link(client, monkeypatch, other.headers, _who()) == "linked_elsewhere"


async def test_only_a_google_account_with_the_same_email_connects(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Phase 9.9: a stolen session must not attach someone else's Google
    account, a lasting way in that skips the authenticator."""
    me = await _register(client, "pat@company.example")
    other = _who("attacker@company.example", "g-evil")
    assert await _link(client, monkeypatch, me.headers, other) == "email_mismatch"
    assert await _actions("user.identity.link") == []


async def test_disconnecting_needs_another_way_in(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    me = await _register(client, "pat@company.example")
    await _link(client, monkeypatch, me.headers, _who())
    r = await client.delete(f"{API}/auth/google", headers=me.headers)
    assert r.status_code == 200
    assert len(await _actions("user.identity.unlink")) == 1

    _, token = await _invite(client, "only@company.example")
    got = await _tokens(
        client, await _flow(client, monkeypatch, _who("only@company.example", "g-2"), invite=token)
    )
    h = {"Authorization": f"Bearer {got['access_token']}"}
    r = await client.delete(f"{API}/auth/google", headers=h)
    assert (r.status_code, r.json()["error"]["code"]) == (400, "only_way_in")


async def test_a_google_only_account_gets_no_reset_email(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, token = await _invite(client, "pat@company.example")
    await _flow(client, monkeypatch, _who(), invite=token)
    backends.outbox.clear()
    r = await client.post(f"{API}/auth/password-reset", json={"email": "pat@company.example"})
    assert r.status_code == 202, "the same answer as ever (7b.6: 202 for everyone)"
    assert [m for m in backends.outbox if m.kind == "password_reset"] == []


# ── auto-join ────────────────────────────────────────────────


async def test_auto_join_is_the_owners_and_makes_employees(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await _register(client, "owner@example.com")
    org = (await client.post(f"{API}/orgs", json={"name": "Acme"}, headers=owner.headers)).json()
    url = f"{API}/orgs/{org['id']}/settings"
    h = {**owner.headers, "X-Org-Id": org["id"]}

    monkeypatch.setattr(settings, "google_allowed_domain", "")
    r = await client.patch(url, json={"google_auto_join": True}, headers=h)
    assert (r.status_code, r.json()["error"]["code"]) == (400, "auto_join_needs_domain")
    monkeypatch.setattr(settings, "google_allowed_domain", DOMAIN)
    assert (await client.patch(url, json={"google_auto_join": True}, headers=h)).status_code == 200

    r = await _flow(client, monkeypatch, _who())
    got = await _tokens(client, r)
    me = (
        await client.get(
            f"{API}/auth/me", headers={"Authorization": f"Bearer {got['access_token']}"}
        )
    ).json()
    assert [(m["org_id"], m["role"]) for m in me["memberships"]] == [(org["id"], "employee")]
    assert [m["role"] for m in await _actions("user.jit_provision")] == ["employee"]

    # Another Google Workspace's account doesn't qualify.
    stranger = _who("x@elsewhere.example", "g-9", domain="elsewhere.example")
    assert _outcome(await _flow(client, monkeypatch, stranger)) == "no_invite"


# ── Google's token, checked ──────────────────────────────────


@pytest.fixture
def signer(monkeypatch: pytest.MonkeyPatch) -> Any:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    class Keys:
        def get_signing_key_from_jwt(self, _token: str) -> Any:
            return type("K", (), {"key": key.public_key()})()

    monkeypatch.setattr(google, "_keys", Keys)

    def sign(**over: Any) -> str:
        now = datetime.now(UTC)
        claims = {
            "iss": "https://accounts.google.com",
            "aud": CLIENT_ID,
            "sub": "g-111",
            "email": "pat@company.example",
            "email_verified": True,
            "hd": DOMAIN,
            "nonce": "n-1",
            "iat": now,
            "exp": now + timedelta(minutes=5),
            **over,
        }
        claims = {k: v for k, v in claims.items() if v is not None}
        return jwt.encode(claims, key, algorithm="RS256")

    return sign


def test_a_good_token_says_who(signer: Any) -> None:
    who = google.verify(signer(), "n-1")
    assert (who.subject, who.email, who.domain) == ("g-111", "pat@company.example", DOMAIN)


@pytest.mark.parametrize(
    ("over", "nonce", "code"),
    [
        ({"aud": "someone-elses-app"}, "n-1", "token"),
        ({"iss": "https://evil.example"}, "n-1", "token"),
        ({"exp": datetime.now(UTC) - timedelta(minutes=1)}, "n-1", "token"),
        ({}, "another-nonce", "token"),
        ({"email_verified": False}, "n-1", "unverified"),
        ({"hd": None}, "n-1", "domain"),
        ({"hd": "elsewhere.example"}, "n-1", "domain"),
        ({"hd": None, "email": "pat@company.example"}, "n-1", "domain"),
    ],
)
def test_a_token_that_doesnt_check_out_is_refused(
    signer: Any, over: dict[str, Any], nonce: str, code: str
) -> None:
    with pytest.raises(GoogleRefused) as refused:
        google.verify(signer(**over), nonce)
    assert refused.value.code == code


def test_a_token_signed_by_someone_else_is_refused(signer: Any) -> None:
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(UTC)
    forged = jwt.encode(
        {
            "iss": "https://accounts.google.com",
            "aud": CLIENT_ID,
            "sub": "g-1",
            "nonce": "n-1",
            "email": "a@company.example",
            "email_verified": True,
            "hd": DOMAIN,
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        other,
        algorithm="RS256",
    )
    with pytest.raises(GoogleRefused):
        google.verify(forged, "n-1")


def test_the_page_after_signing_in_stays_on_this_site() -> None:
    from app.api.routes.google_auth import _safe_next

    assert _safe_next("/chat") == "/chat"
    for bad in (
        "https://evil.example",
        "//evil.example",
        "/\\evil.example",
        "/\t/evil.example",
        None,
        "",
    ):
        assert _safe_next(bad) is None


def test_the_preflight_checks_google_is_set_up_whole() -> None:
    def findings(**over: Any) -> dict[str, str]:
        report = preflight.Report()
        preflight.review_google(_production(**over), report)
        return {f.name: f.level for f in report.findings}

    assert findings() == {}
    assert findings(google_client_id="x") == {"GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET": "fail"}
    both = {"google_client_id": "x", "google_client_secret": "y"}
    assert findings(**both) == {"GOOGLE_ALLOWED_DOMAIN": "warn"}
    assert findings(**both, google_allowed_domain=DOMAIN) == {}
