"""What the security pass found and closed (task 6.3).

Each test is an attack that worked before the pass and the rule that stops
it now. The guards with files of their own keep their regressions there:
`test_ssrf.py` (outbound requests), `test_sql_guard.py` (SQL), and
`test_tenant_isolation.py` (every route, from another org).
"""

from __future__ import annotations

import base64
import logging
import sqlite3
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import structlog
from app import logging as app_logging
from app.agent import approvals as agent_approvals
from app.agent.options import build_claude_options, build_runtime_spec
from app.config import Settings, settings
from app.db.session import get_sessionmaker
from app.mcp.limits import SandboxLimits
from app.mcp.runner import Session, SessionRequest
from app.models.approval import ApprovalStatus
from app.schemas.assistant_config import AssistantConfig
from app.schemas.db_connection import DbConnectionCreate
from app.schemas.mcp_server import check_env
from app.security.redact import REDACTED, redact_event, strip_secrets
from app.services import approvals as approvals_svc
from app.services import auth as auth_svc
from app.services import db_connections as db_svc
from app.services.mcp_discovery import normalize_tools
from httpx import AsyncClient
from pydantic import ValidationError

from test_orgs import Actor, _register  # type: ignore[import-not-found]

pytestmark = pytest.mark.anyio

API = "/api/v1"


# ── people and orgs for the role tests ───────────────────────


async def _team(client: AsyncClient) -> tuple[dict[str, str], dict[str, str], dict[str, str], str]:
    """An org with an owner, an admin and a plain member. Returns their
    headers (with the org) and the org id."""
    owner = await _register(client, "sec-owner@example.com")
    org = (await client.post(f"{API}/orgs", json={"name": "Sec"}, headers=owner.headers)).json()
    o = {**owner.headers, "X-Org-Id": org["id"]}

    async def join(email: str, role: str) -> dict[str, str]:
        person = await _register(client, email)
        inv = await client.post(
            f"{API}/orgs/{org['id']}/invites", json={"email": email, "role": role}, headers=o
        )
        assert inv.status_code == 201, inv.text
        token = inv.json()["accept_url"].rsplit("/", 1)[-1]
        accepted = await client.post(f"{API}/invites/{token}/accept", headers=person.headers)
        assert accepted.status_code == 200, accepted.text
        return {**person.headers, "X-Org-Id": org["id"]}

    admin = await join("sec-admin@example.com", "admin")
    member = await join("sec-member@example.com", "member")
    return o, admin, member, org["id"]


async def _assistant(client: AsyncClient, headers: dict[str, str], name: str = "A") -> str:
    r = await client.post(f"{API}/assistants", json={"name": name}, headers=headers)
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


# ── a SQLite connection is a file the server opens ───────────


async def test_the_platforms_own_database_cannot_be_registered_as_a_connection(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    """Before: any signed-in user could point a connection at the platform's
    SQLite file and read every user's password hash through it."""
    own = settings.database_url.split(":///", 1)[1]
    assert Path(own).exists()  # noqa: ASYNC240
    aid = await _assistant(client, org_headers)
    for path in (own, str(Path(own).parent / "." / Path(own).name)):
        r = await client.post(
            f"{API}/assistants/{aid}/db-connections",
            json={"name": "x", "engine": "sqlite", "database": path},
            headers=org_headers,
        )
        assert r.status_code == 400, r.text
        assert r.json()["error"]["code"] == "sqlite_path_not_allowed"


async def test_sqlite_connections_are_confined_to_the_configured_folder(
    client: AsyncClient,
    org_headers: dict[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allowed, outside = tmp_path / "dbs", tmp_path / "elsewhere"
    allowed.mkdir()
    outside.mkdir()
    for folder in (allowed, outside):
        sqlite3.connect(folder / "shop.db").close()
    monkeypatch.setattr(settings, "db_sqlite_dir", str(allowed))
    aid = await _assistant(client, org_headers)
    url = f"{API}/assistants/{aid}/db-connections"

    def body(path: Path) -> dict[str, Any]:
        return {"name": path.parent.name, "engine": "sqlite", "database": str(path)}

    inside = await client.post(url, json=body(allowed / "shop.db"), headers=org_headers)
    assert inside.status_code == 201, inside.text
    for escape in (outside / "shop.db", allowed / ".." / "elsewhere" / "shop.db"):
        r = await client.post(url, json=body(escape), headers=org_headers)
        assert r.status_code == 400 and "DB_SQLITE_DIR" in r.json()["error"]["message"], r.text
    # Moving an existing connection out of the folder is refused too.
    moved = await client.patch(
        f"{url}/{inside.json()['id']}",
        json={"database": str(outside / "shop.db")},
        headers=org_headers,
    )
    assert moved.status_code == 400, moved.text


def test_production_refuses_sqlite_connections_without_a_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "shop.db"
    sqlite3.connect(db).close()
    db_svc.check_sqlite_path(str(db))  # fine outside production
    monkeypatch.setattr(settings, "app_env", "production")
    with pytest.raises(Exception, match="switched off"):
        db_svc.check_sqlite_path(str(db))


async def test_a_connection_saved_before_the_rule_is_refused_when_used(
    client: AsyncClient,
    org_headers: dict[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = tmp_path / "old.db"
    sqlite3.connect(db).close()
    aid = await _assistant(client, org_headers)
    made = await client.post(
        f"{API}/assistants/{aid}/db-connections",
        json={"name": "old", "engine": "sqlite", "database": str(db)},
        headers=org_headers,
    )
    assert made.status_code == 201
    monkeypatch.setattr(settings, "db_sqlite_dir", str(tmp_path / "only-here"))
    tested = await client.post(
        f"{API}/assistants/{aid}/db-connections/{made.json()['id']}:test", headers=org_headers
    )
    assert tested.status_code == 400, tested.text
    assert tested.json()["error"]["code"] == "sqlite_path_not_allowed"


# ── roles ────────────────────────────────────────────────────


async def test_an_admin_cannot_invite_someone_above_their_own_rank(client: AsyncClient) -> None:
    """Before: an admin invited an address of their own as owner, accepted
    it, and demoted the founder."""
    owner, admin, member, org = await _team(client)
    url = f"{API}/orgs/{org}/invites"
    up = await client.post(url, json={"email": "sock@example.com", "role": "owner"}, headers=admin)
    assert up.status_code == 403 and up.json()["error"]["code"] == "invite_role_not_allowed"
    same = await client.post(url, json={"email": "a2@example.com", "role": "admin"}, headers=admin)
    below = await client.post(
        url, json={"email": "m2@example.com", "role": "member"}, headers=admin
    )
    assert (same.status_code, below.status_code) == (201, 201)
    by_owner = await client.post(
        url, json={"email": "o2@example.com", "role": "owner"}, headers=owner
    )
    assert by_owner.status_code == 201
    assert (
        await client.post(url, json={"email": "x@example.com", "role": "member"}, headers=member)
    ).status_code == 403


async def _pending(client: AsyncClient, headers: dict[str, str], aid: str) -> tuple[str, str]:
    conv = await client.post(f"{API}/assistants/{aid}/conversations", json={}, headers=headers)
    assert conv.status_code == 201, conv.text
    async with get_sessionmaker()() as s:
        row = await approvals_svc.create(
            s,
            conversation_id=uuid.UUID(conv.json()["id"]),
            org_id=uuid.UUID(headers["X-Org-Id"]),
            tool_name="mcp__caps__sql_query",
            tool_input={"sql": "DELETE FROM orders"},
            risk="high",
            rationale="writes",
        )
        return str(conv.json()["id"]), str(row.id)


async def test_only_the_person_in_the_conversation_or_an_admin_decides_an_approval(
    client: AsyncClient,
) -> None:
    """Before: any member who could read a teammate's conversation could
    approve the change it was waiting on."""
    owner, admin, member, _org = await _team(client)
    aid = await _assistant(client, owner)

    async def resolve(who: dict[str, str], approval_id: str) -> int:
        r = await client.post(
            f"{API}/approvals/{approval_id}:resolve", json={"decision": "approved"}, headers=who
        )
        return r.status_code

    cid, first = await _pending(client, owner, aid)
    # The member can see it is pending (members read conversations)...
    seen = await client.get(f"{API}/conversations/{cid}/approvals", headers=member)
    assert [a["id"] for a in seen.json()["items"]] == [first]
    # ...and cannot decide it.
    assert await resolve(member, first) == 403
    async with get_sessionmaker()() as s:
        assert (await approvals_svc.get(s, uuid.UUID(first))).status is ApprovalStatus.pending
    assert await resolve(admin, first) == 200, "an admin reviewing a teammate's write"

    # The person whose conversation it is decides their own.
    _, mine = await _pending(client, member, aid)
    assert await resolve(member, mine) == 200


async def test_only_an_editor_starts_a_conversation_as_an_end_user(client: AsyncClient) -> None:
    """Before: a member named any end user's reference, and the assistant's
    memory tool read (and could rewrite) what it remembered about them."""
    owner, _admin, member, _org = await _team(client)
    aid = await _assistant(client, owner)
    url = f"{API}/assistants/{aid}/conversations"
    as_customer = {"external_user_ref": "cust-1"}
    refused = await client.post(url, json=as_customer, headers=member)
    assert refused.status_code == 403, refused.text
    assert (await client.post(url, json={}, headers=member)).status_code == 201
    assert (await client.post(url, json=as_customer, headers=owner)).status_code == 201


async def test_a_local_command_server_is_an_admins_to_add_and_start(client: AsyncClient) -> None:
    """Before: any member could create an assistant, register `sh -c ...`
    as an MCP server on it, and have the runner start it."""
    _owner, admin, member, _org = await _team(client)
    command = {"name": "files", "transport": "stdio", "command": "sh", "args": ["-c", "id"]}
    remote = {"name": "remote", "transport": "http", "url": "https://mcp.example.com/mcp"}

    mine = await _assistant(client, member, "Member's")
    url = f"{API}/assistants/{mine}/mcp-servers"
    refused = await client.post(url, json=command, headers=member)
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "insufficient_role"
    assert (await client.post(url, json=remote, headers=member)).status_code == 201

    made = await client.post(url, json=command, headers=admin)
    assert made.status_code == 201, made.text
    sid = made.json()["id"]
    # The member edits this assistant, and still can't change or start it.
    for method, path, body in (
        ("PATCH", f"{url}/{sid}", {"args": ["-c", "cat /proc/1/environ"]}),
        ("POST", f"{url}/{sid}:health", None),
        ("POST", f"{url}/{sid}:discover-tools", None),
    ):
        r = await client.request(method, path, json=body, headers=member)
        assert r.status_code == 403, f"{method} {path}: {r.status_code}"


# ── signing in ───────────────────────────────────────────────


async def test_an_unknown_address_costs_a_password_hash_like_a_wrong_password(
    client: AsyncClient, registered: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before: an unknown address returned at once, so the response time
    said whether an account exists."""
    verified: list[str] = []
    real = auth_svc.verify_password

    def counting(password: str, hashed: str) -> bool:
        verified.append(hashed)
        return real(password, hashed)

    monkeypatch.setattr(auth_svc, "verify_password", counting)
    for email in ("nobody@example.com", registered["email"]):
        r = await client.post(f"{API}/auth/login", json={"email": email, "password": "wrong-one"})
        assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_credentials"
    assert len(verified) == 2, "one hash check each"
    assert verified[0] != verified[1] and verified[0].startswith("$argon2")


async def test_a_refresh_token_is_claimed_once_even_under_a_race(
    client: AsyncClient, registered: dict[str, str]
) -> None:
    """Two requests holding the same token: the second finds it claimed, as
    it would between the first one's read and its write."""
    from app.models.refresh_token import RefreshToken
    from sqlalchemy import update

    token = registered["refresh_token"]
    real = auth_svc.issue_tokens
    raced: list[bool] = []

    async def other_request_wins(session: Any, **kw: Any) -> Any:
        raced.append(True)
        return await real(session, **kw)

    async with get_sessionmaker()() as s:
        # The other request's claim lands first.
        claims = auth_svc.decode_refresh_token(token)
        row = await s.scalar(
            __import__("sqlalchemy").select(RefreshToken).where(RefreshToken.jti == claims.jti)
        )
        assert row is not None and row.is_active
        await s.execute(
            update(RefreshToken)
            .where(RefreshToken.id == row.id)
            .values(used_at=__import__("datetime").datetime.now(__import__("datetime").UTC))
            .execution_options(synchronize_session=False)
        )
        # This session still holds the row as unused, exactly as a request
        # that read it a moment earlier would.
        assert row.used_at is None
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(auth_svc, "issue_tokens", other_request_wins)
            with pytest.raises(Exception, match="already used"):
                await auth_svc.rotate_refresh_token(s, token=token)
        assert raced == [], "no second pair was issued"


async def test_a_crashed_turn_tells_the_browser_nothing_about_why(
    client: AsyncClient, org_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import chat as chat_svc

    def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("could not connect to postgresql://app:Pfake123@db.internal/x")

    monkeypatch.setattr(chat_svc, "run_message", boom)
    aid = await _assistant(client, org_headers)
    conv = await client.post(f"{API}/assistants/{aid}/conversations", json={}, headers=org_headers)
    r = await client.post(
        f"{API}/conversations/{conv.json()['id']}/messages",
        json={"text": "hi"},
        headers=org_headers,
    )
    assert "internal_error" in r.text
    assert "Pfake123" not in r.text and "db.internal" not in r.text and "postgresql" not in r.text


# ── secrets in text, logs and what is stored ─────────────────


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("redis://:Pfake123@localhost:6379/0", "Pfake123"),
        ("postgresql://user:pa/ss-Pfake@host/db", "pa/ss-Pfake"),
        ("Authorization: Bearer abc.def.ghi", "abc.def.ghi"),
        ("Authorization: Basic dXNlcjpQZmFrZTEyMw==", "dXNlcjpQZmFrZTEyMw"),
        ("x-api-key: FAKEfake12345", "FAKEfake12345"),
        ("https://api.example.com/x?password=Pfake123&page=2", "Pfake123"),
        ("https://api.example.com/x?api_key=FAKEKEY123", "FAKEKEY123"),
        ("host=db user=app password=Pfake123", "Pfake123"),
        ('{"a": {"password": "Pfake123"}}', "Pfake123"),
        ('{"client_secret": "csFAKE", "access_token": "atFAKE"}', "atFAKE"),
        ("aws_secret_access_key=wJalrFAKE/K7MDENG", "wJalrFAKE"),
        ("APP_KEK=abcdBASE64keyFAKE==", "abcdBASE64keyFAKE"),
        ("Set-Cookie: session=abc123FAKE; Path=/", "abc123FAKE"),
        ("sk_live_FAKEfake12345678", "FAKEfake12345678"),
        ("key pa-FAKEfake1234567890abcdefgh here", "FAKEfake1234567890"),
    ],
)
def test_credentials_are_recognised_by_where_they_sit(text: str, secret: str) -> None:
    out = strip_secrets(text)
    assert secret not in out and REDACTED in out, out


@pytest.mark.parametrize(
    "text",
    [
        "To reset your password: see the help page.",
        "The secret is out. Cookie: 200g flour, 100g sugar",
        '{"next_page_token": "keepme", "tokens_in": 5, "max_tokens": 100}',
        "https://host:8443/users/@me",
        "A token of appreciation = priceless",
        "the bearer of bad news",
    ],
)
def test_ordinary_text_is_left_alone(text: str) -> None:
    assert strip_secrets(text) == text


def test_log_fields_are_redacted_by_name_shape_and_depth() -> None:
    event = {
        "event": "x",
        "db_password": "p",
        "client_secret": "s",
        "jwt_secret": "j",
        "app_kek": "k",
        "mcp_runner_token": "t",
        "headers": {"X-Api-Key": "k", "Accept": "json"},
        "env": {"GITHUB_TOKEN": "g", "LOG_LEVEL": "info"},
        "tokens_in": 5,
        "error": ValueError("postgresql://u:Pfake123@h/db"),
        "body": b"password=Pfake123",
        "deep": {"a": {"b": {"c": {"d": {"password": "x"}}}}},
    }
    out = redact_event(None, "", event)
    for key in ("db_password", "client_secret", "jwt_secret", "app_kek", "mcp_runner_token"):
        assert out[key] == REDACTED, key
    assert out["headers"] == {"X-Api-Key": REDACTED, "Accept": "json"}
    assert out["env"] == {"GITHUB_TOKEN": REDACTED, "LOG_LEVEL": "info"}
    assert out["tokens_in"] == 5
    assert "Pfake123" not in out["error"] and "Pfake123" not in out["body"]
    # Too deep to look into is not let through.
    assert "password" not in str(out["deep"]) and REDACTED in str(out["deep"])


@pytest.mark.parametrize("fmt", ["json", "console"])
def test_a_traceback_in_the_log_is_scrubbed_and_shows_no_locals(
    fmt: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before: the console printed each frame's local variables, unredacted,
    and the JSON log had no traceback at all (`"exc_info": true`)."""
    root = logging.getLogger()
    saved = (root.handlers[:], root.level)
    monkeypatch.setitem(app_logging._state, "configured", False)
    structlog.reset_defaults()
    try:
        app_logging.configure_logging("INFO", fmt)
        log = app_logging.get_logger("test.security")

        def connect() -> None:
            dsn = "postgresql://app:Pfake123@db.internal/x"  # a local in the frame
            raise RuntimeError(f"could not connect to {dsn}")

        try:
            connect()
        except RuntimeError:
            log.exception("unhandled_exception")
            logging.getLogger("sqlalchemy.pool").exception("pool broke")
    finally:
        root.handlers[:], _ = saved
        root.setLevel(saved[1])
        structlog.reset_defaults()
        app_logging._state["configured"] = False
        app_logging.configure_logging(settings.log_level, settings.log_format)
    out = capsys.readouterr().out
    assert "Pfake123" not in out, out
    assert out.count("RuntimeError") >= 2, "both lines carry their traceback"
    assert "could not connect to postgresql://app:<redacted>@db.internal/x" in out


def test_tool_inputs_are_redacted_by_any_credential_like_key() -> None:
    shown = agent_approvals.redact(
        {
            "X-Auth-Token": "a",
            "Proxy-Authorization": "b",
            "apikey": "c",
            "access_token": "d",
            "client_secret": "e",
            "db_password": "f",
            "query": "refunds",
            "page_token": "g",
            "nested": [{"refresh_token": "h"}],
        }
    )
    assert shown["query"] == "refunds"
    hidden = {k for k, v in shown.items() if v == REDACTED}
    assert hidden == {
        "X-Auth-Token",
        "Proxy-Authorization",
        "apikey",
        "access_token",
        "client_secret",
        "db_password",
        "page_token",
    }
    assert shown["nested"] == [{"refresh_token": REDACTED}]


def test_an_approval_card_masks_credentials_in_the_url_and_body() -> None:
    card = agent_approvals.describe(
        "mcp__caps__http_request",
        {
            "method": "post",
            "url": "https://api.example.com/x?api_key=FAKEKEY123",
            "headers": {"Authorization": "Bearer abcdefgh12345678", "Accept": "json"},
            "body": '{"password": "Pfake123", "note": "keep"}',
        },
    )
    assert "FAKEKEY123" not in card and "Pfake123" not in card and "abcdefgh" not in card
    assert "POST https://api.example.com/x?api_key=<redacted>" in card
    assert "Accept: json" in card and '"note": "keep"' in card


def test_an_approval_card_names_a_servers_tool_as_the_servers() -> None:
    """A server's tool literally named `mcp__caps__sql_query` used to be shown
    as the platform's own `sql_query`."""
    card = agent_approvals.describe("mcp__evil__mcp__caps__sql_query", {"sql": "x"})
    assert card.startswith("evil: mcp__caps__sql_query(")
    offered = [SimpleNamespace(name="mcp__caps__sql_query"), SimpleNamespace(name="search")]
    kept, skipped = normalize_tools(offered)
    assert [t["name"] for t in kept] == ["search"]
    assert "double underscore" in skipped[0]


async def test_the_access_log_names_the_route_not_the_invite_token(
    client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    token = "inviteTOKENthatIsTheWholeCredential0123456789"
    with caplog.at_level(logging.INFO, logger="api.access"):
        await client.get(f"{API}/invites/{token}")
    lines = [r.getMessage() for r in caplog.records if r.name == "api.access"]
    assert lines, "the request was logged"
    assert token not in " ".join(lines)
    assert "/invites/{token}" in " ".join(lines)


async def test_every_response_tells_the_browser_not_to_frame_or_sniff_it(
    client: AsyncClient,
) -> None:
    for path in ("/healthz", f"{API}/meta/config-schema", f"{API}/assistants"):
        r = await client.get(path)
        assert r.headers["x-content-type-options"] == "nosniff", path
        assert r.headers["x-frame-options"] == "DENY"
        assert r.headers["referrer-policy"] == "no-referrer"
        assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    # A browser is never told to send cookies along: sign-in is a header.
    pre = await client.options(
        f"{API}/assistants",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-credentials" not in pre.headers


def test_connection_options_cannot_carry_a_credential_at_any_depth() -> None:
    def make(**extra: Any) -> DbConnectionCreate:
        return DbConnectionCreate(
            name="x", engine="postgres", host="db.example.com", database="app", **extra
        )

    assert make(options={"sslmode": "require", "connect_timeout": 5}, ssl={"mode": "verify-full"})
    for bad in (
        {"options": {"auth": {"password": "Pfake123"}}},
        {"options": {"authToken": "tFAKE12345"}},
        {"options": {"server": "postgresql://u:Pfake123@h/db"}},
        {"ssl": {"client_secret": "x"}},
        {"ssl": {"key": "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----"}},
    ):
        with pytest.raises(ValidationError) as refused:
            make(**bad)
        message = refused.value.errors()[0]["msg"]
        assert "may not contain" in message
        assert "Pfake123" not in message, "the error names the key, never the value"


def test_the_agents_cli_does_not_inherit_the_servers_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def allow(*_a: Any, **_k: Any) -> Any:
        return None

    # Set here, not read from a developer's .env: a failure must never print one.
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    options = build_claude_options(build_runtime_spec(AssistantConfig()), allow)
    for name in ("APP_KEK", "JWT_SECRET", "DATABASE_URL", "S3_SECRET_KEY", "MCP_RUNNER_TOKEN"):
        assert options.env[name] == "", name
    assert "ANTHROPIC_API_KEY" not in options.env, "no key set: nothing to hand over"


def test_the_cli_is_handed_the_platforms_model_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Not inherited (Phase 7a.6): settings read .env without exporting it,
    and the CLI then signed in with the developer's own Claude login."""

    async def allow(*_a: Any, **_k: Any) -> Any:
        return None

    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-test-only")
    options = build_claude_options(build_runtime_spec(AssistantConfig()), allow)
    assert options.env["ANTHROPIC_API_KEY"] == "sk-ant-test-only"


# ── starting in production ───────────────────────────────────


def _production(**over: Any) -> Settings:
    base: dict[str, Any] = {
        "app_env": "production",
        "jwt_secret": "j" * 48,
        "app_kek": base64.b64encode(b"k" * 32).decode(),
        "anthropic_api_key": "sk-ant-fake",
        "mcp_runner_url": "http://mcp-runner:8100",
        "mcp_runner_token": "r" * 32,
        "s3_secret_key": "a-real-object-store-secret",
        "log_format": "json",
    }
    return Settings(_env_file=None, **{**base, **over})  # type: ignore[call-arg]


def test_production_starts_with_real_settings() -> None:
    assert _production().is_production


@pytest.mark.parametrize(
    ("override", "problem"),
    [
        ({"mcp_allow_insecure_urls": True}, "MCP_ALLOW_INSECURE_URLS"),
        ({"mcp_runner_token": ""}, "MCP_RUNNER_TOKEN"),
        ({"s3_secret_key": "minioadmin"}, "S3_SECRET_KEY"),
        ({"jwt_secret": "short"}, "JWT_SECRET"),
    ],
)
def test_production_refuses_to_start_with_a_development_setting(
    override: dict[str, Any], problem: str
) -> None:
    with pytest.raises(RuntimeError, match=problem):
        _production(**override)


# ── the MCP runner ───────────────────────────────────────────


def test_a_server_cannot_be_given_more_than_the_runner_has() -> None:
    assert SandboxLimits(memory_mb=1536, max_processes=256, max_file_mb=256)
    for over in ({"memory_mb": 8192}, {"max_processes": 1024}, {"max_file_mb": 1024}):
        with pytest.raises(ValidationError):
            SandboxLimits(**over)
    # A row saved under the old ceilings falls back to the default.
    assert SandboxLimits.from_row({"memory_mb": 8192}).memory_mb == SandboxLimits().memory_mb


def test_loader_variables_cannot_be_set_on_a_server() -> None:
    assert check_env({"GITHUB_TOKEN": "x", "NODE_ENV": "production"})
    for key in ("LD_PRELOAD", "LD_LIBRARY_PATH", "DYLD_INSERT_LIBRARIES", "ld_preload"):
        with pytest.raises(ValueError, match="how programs are loaded"):
            check_env({key: "/tmp/x.so"})


def test_the_runner_caps_what_it_is_sent() -> None:
    assert SessionRequest(command="npx", args=["-y", "x"], env={"A": "b"})
    for bad in (
        {"args": ["x" * 8_001]},
        {"env": {"A": "x" * 8_001}},
        {"env": {f"K{i}": "v" for i in range(51)}},
    ):
        with pytest.raises(ValidationError):
            SessionRequest(command="npx", **bad)  # type: ignore[arg-type]


def test_a_servers_last_words_do_not_include_its_own_secrets(tmp_path: Path) -> None:
    """A crashing server often prints its configuration. A database password
    has no recognisable shape, so it is removed by value."""
    (tmp_path / ".stderr.log").write_text(
        "connecting as app with hunter2-db-pass\n"
        "fatal: auth failed for hunter2-db-pass (mode=on)\n",
        encoding="utf-8",
    )
    session = Session(
        id="s",
        token="t",
        label="db",
        limits=SandboxLimits(),
        workdir=tmp_path,
        transport=None,  # type: ignore[arg-type]
        env_values=("hunter2-db-pass",),
    )
    tail = session.stderr_tail()
    assert "hunter2-db-pass" not in tail and tail.count(REDACTED) == 2
    assert "mode=on" in tail, "short values like 'on' are not scrubbed"


_ = Actor  # re-exported for readers of this file's helpers


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        # One opaque token after the scheme: only it goes, the line goes on.
        (
            "Authorization: Bearer abcdef0123456789 then GET /x",
            "Authorization: Bearer <redacted> then GET /x",
        ),
        ('Authorization: Bearer "abc123" next', "Authorization: Bearer <redacted> next"),
        ("proxy-authorization: Basic dXNlcjpwYXNz", "proxy-authorization: Basic <redacted>"),
        # Quoted parameters: the whole rest of the line.
        (
            'Authorization: Digest username="ana", response="6629fae49393a053974509"',
            "Authorization: Digest <redacted>",
        ),
        # No scheme: up to the end of the value, also inside a JSON string.
        ("X-Api-Key: sk-live-abc def", "X-Api-Key: <redacted>"),
        ('{"msg": "x-api-key: abc123", "n": 1}', '{"msg": "x-api-key: <redacted>", "n": 1}'),
    ],
)
def test_a_header_loses_its_credential_and_nothing_else(line: str, expected: str) -> None:
    """Found by the release check (task 6.9): the header rule from this pass
    took everything after the colon, so a log line that went on to say
    something else lost it, and `Bearer` itself."""
    assert strip_secrets(line) == expected
