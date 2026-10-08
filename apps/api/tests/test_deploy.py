"""Deployment (task 6.6): the preflight, sign-up by invitation, and the
production compose file and proxy.

Nothing in CI starts the production stack, so the files that make it are
checked for what they promise: one way in, no default secrets, the
datastores off the internet, the proxy routing what it must and refusing
what it must. A change that publishes Postgres or drops a required secret
fails here.
"""

from __future__ import annotations

import base64
import re
import uuid
import warnings
from pathlib import Path
from typing import Any

import pytest
import yaml
from app import preflight
from app.config import Settings, settings
from app.db.session import get_sessionmaker
from app.models.secret import Secret, SecretKind
from app.security.crypto import generate_kek, seal
from httpx import AsyncClient

pytestmark = pytest.mark.anyio

API = "/api/v1"
ROOT = Path(__file__).resolve().parents[3]
KEK = base64.b64encode(b"k" * 32).decode()


def _production(**over: Any) -> Settings:
    base: dict[str, Any] = {
        "app_env": "production",
        "jwt_secret": "j" * 48,
        "app_kek": KEK,
        "anthropic_api_key": "sk-ant-fake",
        "voyage_api_key": "pa-fake",
        "mcp_runner_url": "http://mcp-runner:8100",
        "mcp_runner_token": "r" * 32,
        "s3_secret_key": "a-real-object-store-secret",
        "log_format": "json",
        "app_base_url": "https://assistant.example.com",
        "cors_origins": "https://assistant.example.com",
        "registration": "invite",
        "metrics_token": "m" * 32,
        "trusted_proxy_hops": 1,
        "database_url": "postgresql+asyncpg://app:pw@postgres:5432/app",
    }
    return Settings(_env_file=None, **{**base, **over})  # type: ignore[call-arg]


# ── the preflight ────────────────────────────────────────────


def _names(report: preflight.Report, level: str) -> list[str]:
    return [f.name for f in report.findings if f.level == level]


def test_a_well_configured_production_server_gets_no_warnings() -> None:
    report = preflight.Report()
    preflight.review(_production(), report)
    assert report.findings == []


@pytest.mark.parametrize(
    ("override", "warned"),
    [
        ({"registration": "open"}, "REGISTRATION"),
        ({"app_base_url": "http://localhost:3000"}, "APP_BASE_URL"),
        ({"cors_origins": "https://a.example,http://127.0.0.1:3000"}, "CORS_ORIGINS"),
        ({"database_url": "sqlite+aiosqlite:///./x.db"}, "DATABASE_URL"),
        ({"voyage_api_key": ""}, "VOYAGE_API_KEY"),
        ({"metrics_token": ""}, "METRICS_TOKEN"),
        ({"trusted_proxy_hops": 0}, "TRUSTED_PROXY_HOPS"),
    ],
)
def test_the_preflight_warns_about_what_is_valid_and_probably_not_meant(
    override: dict[str, Any], warned: str
) -> None:
    report = preflight.Report()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        preflight.review(_production(**override), report)
    assert _names(report, "warn") == [warned]
    assert not report.failed  # a warning never stops the server


def test_the_local_model_on_purpose_is_not_a_warning() -> None:
    report = preflight.Report()
    preflight.review(_production(voyage_api_key="", rag_offline=True), report)
    assert report.findings == []


def test_development_is_not_reviewed() -> None:
    report = preflight.Report()
    preflight.review(Settings(_env_file=None, app_env="dev", registration="open"), report)  # type: ignore[call-arg]
    assert report.findings == []


async def test_a_fatal_check_that_fails_stops_the_start_and_a_minor_one_does_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fine() -> str:
        return ""

    async def down() -> str:
        raise ConnectionRefusedError("postgresql://app:hunter2@db/app refused")

    async def ours() -> str:
        raise RuntimeError("the key does not open the stored credentials")

    async def skipped() -> str:
        return "skip: offline driver"

    monkeypatch.setattr(
        preflight,
        "CHECKS",
        {
            "database": (fine, True),
            "encryption key": (ours, True),
            "agent CLI": (skipped, True),
            "redis": (down, False),
        },
    )
    report = preflight.Report()
    await preflight.probe(report)
    assert report.failed
    assert _names(report, "fail") == ["encryption key"]
    assert _names(report, "warn") == ["redis"]
    assert _names(report, "skip") == ["agent CLI"]
    shown = report.render()
    assert "the key does not open the stored credentials" in shown
    # A driver's message can quote the connection string: only its type.
    assert "hunter2" not in shown and "ConnectionRefusedError" in shown
    assert shown.endswith("cannot start: fix the FAIL lines above")


async def test_a_check_that_hangs_is_a_failure_not_a_hang(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    async def never() -> str:
        await asyncio.sleep(30)
        return ""

    monkeypatch.setattr(preflight, "TIMEOUT_S", 0.05)
    monkeypatch.setattr(preflight, "CHECKS", {"database": (never, True)})
    report = preflight.Report()
    await preflight.probe(report)
    assert report.failed and "no answer" in report.findings[0].detail


async def test_the_key_is_tried_against_what_is_stored(
    org_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """After a restore, or a changed APP_KEK: every stored credential fails
    at the moment someone uses it. The preflight says so at the start."""
    monkeypatch.setattr(settings, "app_kek", KEK)
    assert await preflight._encryption_key() == "nothing stored yet"

    sealed = seal("s3cret", kind=SecretKind.db_password.value)
    async with get_sessionmaker()() as s:
        s.add(
            Secret(
                org_id=uuid.UUID(org_headers["X-Org-Id"]),
                kind=SecretKind.db_password,
                ciphertext=sealed.ciphertext,
                dek_wrapped=sealed.dek_wrapped,
                nonce=sealed.nonce,
            )
        )
        await s.commit()
    assert await preflight._encryption_key() == "opens the stored credentials"

    monkeypatch.setattr(settings, "app_kek", generate_kek())
    with pytest.raises(RuntimeError, match="does not open the stored credentials"):
        await preflight._encryption_key()

    monkeypatch.setattr(settings, "app_kek", "")
    assert (await preflight._encryption_key()).startswith("skip: ")


def test_a_malformed_setting_is_named_without_its_value() -> None:
    with pytest.raises(Exception) as caught:
        Settings(_env_file=None, app_kek="not-base64-and-still-a-secret!!")  # type: ignore[call-arg]
    lines = preflight._validation_lines(caught.value)
    assert lines and lines[0].startswith("APP_KEK: ")
    assert "still-a-secret" not in " ".join(lines)


def test_the_entrypoint_runs_the_preflight_before_the_migrations() -> None:
    script = (ROOT / "docker" / "entrypoint-api.sh").read_bytes()
    # A Windows checkout's CRLF makes `sh` look for a program called "sh\r".
    assert b"\r" not in script
    text = script.decode()
    assert text.index("python -m app.preflight") < text.index("alembic upgrade head")
    assert "sed -i 's/\\r$//'" in (ROOT / "docker" / "api.Dockerfile").read_text(encoding="utf-8")


# ── sign-up by invitation ────────────────────────────────────


async def _register(client: AsyncClient, email: str, **extra: Any) -> Any:
    body = {"email": email, "password": "correct-horse-battery", "name": "N", **extra}
    return await client.post(f"{API}/auth/register", json=body)


async def _invite(client: AsyncClient, owner: dict[str, str], email: str) -> str:
    me = await client.get(f"{API}/auth/me", headers=owner)
    org = me.json()["memberships"][0]["org_id"]
    made = await client.post(
        f"{API}/orgs/{org}/invites", json={"email": email, "role": "member"}, headers=owner
    )
    assert made.status_code == 201, made.text
    return str(made.json()["accept_url"]).rstrip("/").rsplit("/", 1)[-1]


async def test_by_invitation_the_first_account_is_free_and_the_next_needs_an_invite(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "registration", "invite")
    first = await _register(client, "owner@example.com")
    assert first.status_code == 201  # a new install: someone has to be first
    owner = {"Authorization": f"Bearer {first.json()['access_token']}"}

    stranger = await _register(client, "stranger@example.com")
    assert stranger.status_code == 403
    assert stranger.json()["error"]["code"] == "registration_by_invite"

    token = await _invite(client, owner, "friend@example.com")
    # Knowing that an address was invited is not enough...
    assert (await _register(client, "friend@example.com")).status_code == 403
    # ...nor is a link issued to someone else...
    assert (await _register(client, "other@example.com", invite_token=token)).status_code == 403
    assert (await _register(client, "friend@example.com", invite_token="x" * 43)).status_code == 403
    # ...the invited person, with their link, gets an account.
    joined = await _register(client, "Friend@Example.com", invite_token=token)
    assert joined.status_code == 201, joined.text


async def test_a_used_or_expired_invite_no_longer_opens_sign_up(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import UTC, datetime, timedelta

    from app.models.invite import Invite
    from sqlalchemy import update

    monkeypatch.setattr(settings, "registration", "invite")
    first = await _register(client, "owner@example.com")
    owner = {"Authorization": f"Bearer {first.json()['access_token']}"}
    token = await _invite(client, owner, "late@example.com")
    async with get_sessionmaker()() as s:
        await s.execute(update(Invite).values(expires_at=datetime.now(UTC) - timedelta(minutes=1)))
        await s.commit()
    assert (await _register(client, "late@example.com", invite_token=token)).status_code == 403

    token = await _invite(client, owner, "done@example.com")
    async with get_sessionmaker()() as s:
        await s.execute(
            update(Invite)
            .where(Invite.email == "done@example.com")
            .values(accepted_at=datetime.now(UTC))
        )
        await s.commit()
    assert (await _register(client, "done@example.com", invite_token=token)).status_code == 403


async def test_open_sign_up_is_unchanged(client: AsyncClient) -> None:
    assert settings.registration == "open"
    assert (await _register(client, "a@example.com")).status_code == 201
    assert (await _register(client, "b@example.com", invite_token="ignored")).status_code == 201


# ── the production compose file ──────────────────────────────


def _without_comments(path: Path) -> str:
    """The file as Compose reads it: the comments explain the dev defaults
    this file is there to avoid, and name them."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return "\n".join(line for line in lines if not line.lstrip().startswith("#"))


@pytest.fixture(scope="module")
def prod() -> dict[str, Any]:
    return yaml.safe_load((ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8"))


def test_only_the_proxy_is_published(prod: dict[str, Any]) -> None:
    published = {name for name, svc in prod["services"].items() if svc.get("ports")}
    assert published == {"proxy"}


def test_no_secret_has_a_default() -> None:
    text = _without_comments(ROOT / "docker-compose.prod.yml")
    for name in (
        "DOMAIN",
        "POSTGRES_PASSWORD",
        "REDIS_PASSWORD",
        "S3_ACCESS_KEY",
        "S3_SECRET_KEY",
        "JWT_SECRET",
        "APP_KEK",
        "ANTHROPIC_API_KEY",
        "MCP_RUNNER_TOKEN",
    ):
        assert re.search(rf"\$\{{{name}:\?", text), f"{name} is not required"
        assert not re.search(rf"\$\{{{name}:-", text), f"{name} has a default"
    for default in ("minioadmin", "dev-insecure", "app:app@", "rootpw"):
        assert default not in text


def test_the_datastores_have_no_route_out(prod: dict[str, Any]) -> None:
    networks, services = prod["networks"], prod["services"]
    assert networks["data"]["internal"] is True and networks["mcp"]["internal"] is True
    assert services["postgres"]["networks"] == ["data"]
    assert services["redis"]["networks"] == ["data"]
    assert services["mcp-runner"]["networks"] == ["mcp"]
    # The proxy cannot reach the database, and the runner cannot reach anything.
    assert services["proxy"]["networks"] == ["edge"]
    assert "data" not in services["web"]["networks"]


def test_the_api_starts_as_production_behind_one_proxy(prod: dict[str, Any]) -> None:
    env = prod["x-api-env"]
    assert env["APP_ENV"] == "production"
    assert env["REGISTRATION"] == "${REGISTRATION:-invite}"
    assert env["TRUSTED_PROXY_HOPS"] == "${TRUSTED_PROXY_HOPS:-1}"
    assert env["CORS_ORIGINS"] == "https://${DOMAIN}"
    assert env["S3_PUBLIC_ENDPOINT"] == "https://${DOMAIN}"
    # The browser bundle is built for the same origin the proxy serves.
    assert prod["services"]["web"]["build"]["args"]["NEXT_PUBLIC_API_URL"] == "https://${DOMAIN}"


def test_every_long_running_container_is_locked_down(prod: dict[str, Any]) -> None:
    for name, svc in prod["services"].items():
        if name in ("minio-init", "minio-owner"):
            continue  # run once and exit
        assert svc["restart"] == "unless-stopped", name
        assert svc["security_opt"] == ["no-new-privileges:true"], name
        assert svc["logging"]["options"]["max-size"], name
    for name in ("proxy", "web", "api", "worker", "mcp-runner"):
        assert prod["services"][name]["cap_drop"] == ["ALL"], name
    assert prod["services"]["proxy"]["cap_add"] == ["NET_BIND_SERVICE"]
    assert prod["services"]["mcp-runner"]["read_only"] is True
    # One container migrates; the other must not race it.
    assert prod["services"]["worker"]["environment"]["RUN_MIGRATIONS"] == "0"


def test_the_redis_password_is_not_on_a_command_line(prod: dict[str, Any]) -> None:
    redis = prod["services"]["redis"]
    assert "${REDIS_PASSWORD" not in " ".join(redis["command"])
    assert "$$REDIS_PASSWORD" in " ".join(redis["command"])


def test_the_env_example_names_every_required_value() -> None:
    compose = _without_comments(ROOT / "docker-compose.prod.yml")
    example = (ROOT / "deploy" / "production.env.example").read_text(encoding="utf-8")
    required = set(re.findall(r"\$\{([A-Z0-9_]+):\?", compose))
    offered = set(re.findall(r"^([A-Z0-9_]+)=", example, re.M))
    assert required <= offered, required - offered
    # And it ships no values for them.
    for name in required:
        assert re.search(rf"^{name}=$", example, re.M), f"{name} has a value in the example"


# ── the proxy ────────────────────────────────────────────────


def test_the_proxy_routes_what_it_must_and_refuses_metrics() -> None:
    caddy = (ROOT / "deploy" / "proxy" / "Caddyfile").read_text(encoding="utf-8")
    assert "{$SITE_ADDRESS} {" in caddy
    assert "Strict-Transport-Security" in caddy
    assert re.search(r"handle /metrics \{\s*respond \"Not found\" 404", caddy)
    api = caddy[caddy.index("handle @api") :]
    assert "reverse_proxy api:8000" in api
    # A chat answer is a stream: a buffering proxy turns it into one silence.
    assert "flush_interval -1" in api
    assert "@api path /api/* /healthz /readyz" in caddy
    # Citation links: reads only, one bucket.
    files = caddy[caddy.index("@files {") : caddy.index("handle @files")]
    assert "method GET HEAD" in files and "path /{$S3_BUCKET}/*" in files
    # Phase 7a.1: files are served on the app's origin, so the browser must
    # not guess their type and nothing in them may run (a second wall behind
    # the API's choice of type, storage/file_types.py).
    handle = caddy[caddy.index("handle @files") : caddy.index("reverse_proxy minio:9000")]
    served = handle[handle.index("header {") :]
    assert "X-Content-Type-Options nosniff" in served
    assert "script-src 'none'" in served and "frame-ancestors 'none'" in served
    # Never a rule that stops the browser's own PDF viewer.
    assert "sandbox" not in served and "object-src" not in served
    assert caddy.count("{") == caddy.count("}")


# ── backup and restore (task 6.7) ────────────────────────────

BACKUP = ROOT / "deploy" / "backup"
SCRIPTS = ("lib.sh", "backup.sh", "verify.sh", "restore.sh")


def test_the_backup_scripts_are_shell_the_server_can_run() -> None:
    import shutil
    import subprocess

    for name in SCRIPTS:
        raw = (BACKUP / name).read_bytes()
        assert b"\r" not in raw, f"{name} has Windows line endings"
    # On Windows, Git's shell when it is not on the PATH (as under PowerShell).
    git_sh = Path("C:/Program Files/Git/bin/sh.exe")
    sh = shutil.which("sh") or (str(git_sh) if git_sh.is_file() else None)
    if sh is None:  # pragma: no cover - a machine without a POSIX shell
        pytest.skip("no sh on this machine")
    for name in SCRIPTS:
        checked = subprocess.run([sh, "-n", str(BACKUP / name)], capture_output=True, text=True)
        assert checked.returncode == 0, f"{name}: {checked.stderr}"


def test_a_backup_is_only_a_backup_once_it_has_its_manifest() -> None:
    backup = (BACKUP / "backup.sh").read_text(encoding="utf-8")
    # The manifest is written last, after the dump was read back and the
    # files copied: a run that died half-way leaves a folder without one.
    assert backup.index("pg_restore --list") < backup.index('cat > "$DEST/manifest.json"')
    assert backup.index("mc mirror") < backup.index('cat > "$DEST/manifest.json"')
    for name in ("verify.sh", "restore.sh"):
        script = (BACKUP / name).read_text(encoding="utf-8")
        assert "has no manifest.json" in script, name
        # And the dump must still be the one the manifest describes.
        assert '= "$WANT_SHA" ] || die' in script, name


def test_a_restore_asks_first_and_verifying_never_touches_the_live_database() -> None:
    restore = (BACKUP / "restore.sh").read_text(encoding="utf-8")
    asked = restore.index('[ "$answer" = "$DB_NAME" ] || die')
    assert asked < restore.index("DROP DATABASE")
    assert restore.index("stop api worker") < restore.index("DROP DATABASE")
    verify = (BACKUP / "verify.sh").read_text(encoding="utf-8")
    assert 'SCRATCH="restore_verify_' in verify
    # Every statement that changes anything names the scratch database.
    for line in verify.splitlines():
        if "DROP DATABASE" in line or "CREATE DATABASE" in line or "pg_restore -U" in line:
            assert "$SCRATCH" in line, line
    assert "trap cleanup EXIT" in verify


def test_the_backup_scripts_never_put_a_secret_on_a_command_line() -> None:
    lib = (BACKUP / "lib.sh").read_text(encoding="utf-8")
    # MinIO's credentials go to the one-off container through its
    # environment (`-e NAME` with no value), not as arguments.
    assert "-e MC_USER -e MC_PASS" in lib
    assert '"$MC_USER" "$MC_PASS"' in lib  # expanded inside the container
    for name in SCRIPTS:
        text = (BACKUP / name).read_text(encoding="utf-8")
        assert "APP_KEK=" not in text and "PGPASSWORD=" not in text, name
    # And the key is said, in the script's own output, not to be in there.
    assert "APP_KEK is not in this backup" in (BACKUP / "backup.sh").read_text(encoding="utf-8")


def test_the_stack_needs_one_minio_image(prod: dict[str, Any]) -> None:
    # The bucket job, the volume's owner and the backups use `mc` and `sh`
    # from the server image: one image to have and pin.
    dev = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    for stack in (prod, dev):
        image = stack["services"]["minio"]["image"]
        for name in ("minio-init", "minio-owner"):
            assert stack["services"][name]["image"] == image, name


def test_the_object_store_is_a_maintained_build_pinned_by_digest(prod: dict[str, Any]) -> None:
    """Phase 7a.6: minio/minio is no longer published and its code is
    archived with unpatched CVEs. Chainguard rebuilds it from source; a digest
    means a rebuild changes nothing until someone tests and bumps it."""
    dev = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    obs = yaml.safe_load((ROOT / "docker-compose.observability.yml").read_text(encoding="utf-8"))
    images = [
        dev["services"]["minio"]["image"],
        obs["services"]["langfuse-minio"]["image"],
        # The production default, inside ${MINIO_IMAGE:-...}.
        prod["services"]["minio"]["image"].removeprefix("${MINIO_IMAGE:-").removesuffix("}"),
    ]
    for image in images:
        assert image.startswith("cgr.dev/chainguard/minio@sha256:"), image
    assert len(set(images)) == 1, "one tested build everywhere"
    assert "minio/minio" not in _without_comments(ROOT / "docker-compose.prod.yml")


def test_the_volume_owner_step_does_one_thing_with_one_capability(
    prod: dict[str, Any],
) -> None:
    """Chainguard's MinIO runs as uid 65532; a volume the old image wrote is
    root's. The step that hands it over runs as root, so it gets only CHOWN,
    no network, and runs before the server."""
    owner = prod["services"]["minio-owner"]
    assert owner["user"] == "0"
    assert owner["cap_drop"] == ["ALL"] and owner["cap_add"] == ["CHOWN"]
    assert owner["network_mode"] == "none"
    assert owner["restart"] == "no"
    assert "chown -R 65532:65532 /data" in owner["entrypoint"][-1]
    wait = prod["services"]["minio"]["depends_on"]["minio-owner"]
    assert wait["condition"] == "service_completed_successfully"


def test_backups_run_mc_as_the_operator_not_the_images_user() -> None:
    lib = (BACKUP / "lib.sh").read_text(encoding="utf-8")
    assert '--user "$(id -u):$(id -g)" -e HOME=/tmp' in lib


def test_the_cli_files_live_on_one_volume_the_api_and_worker_share(prod: dict[str, Any]) -> None:
    """Phase 7a.5: the CLI's transcripts survive a redeploy, and the worker's
    sweep sees the files the API's turns wrote."""
    mount = "agentstate:/var/lib/assistant-studio"
    dev = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    for stack in (prod, dev):
        for name in ("api", "worker"):
            assert mount in stack["services"][name]["volumes"], name
        assert "agentstate" in stack["volumes"]
    image = (ROOT / "docker" / "api.Dockerfile").read_text(encoding="utf-8")
    assert "AGENT_STATE_DIR=/var/lib/assistant-studio" in image
    assert "chown app:app /var/lib/assistant-studio" in image, "the app user can write to it"


def test_a_stop_leaves_time_to_record_running_turns(prod: dict[str, Any]) -> None:
    """uvicorn closes open streams after 10 s, then the app marks its turns
    stopped; Docker waits 30 s before it kills anything."""
    image = (ROOT / "docker" / "api.Dockerfile").read_text(encoding="utf-8")
    assert '"--timeout-graceful-shutdown", "10"' in image
    for name in ("api", "worker"):
        assert prod["services"][name]["stop_grace_period"] == "30s", name


def test_the_image_and_ci_install_the_locked_versions() -> None:
    """Phase 7a.6: no rebuild picks up whatever was released since. The
    image and CI install under constraints.txt, made from a Linux install of
    the versions tested in development."""
    image = (ROOT / "docker" / "api.Dockerfile").read_text(encoding="utf-8")
    assert "apps/api/constraints.txt ./apps/api/" in image, "copied into the build"
    assert "pip install -c ./apps/api/constraints.txt" in image
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "pip install -c constraints.txt -e" in ci
    lock = (ROOT / "apps" / "api" / "constraints.txt").read_text(encoding="utf-8")
    pins = dict(
        line.split("==", 1) for line in lock.splitlines() if line and not line.startswith("#")
    )
    assert len(pins) > 100 and all("@" not in v for v in pins.values())
    assert pins["torch"].endswith("+cpu"), "the CPU build, as the image installs"


def test_the_cli_and_the_sql_classifier_are_pinned_exactly() -> None:
    """claude-agent-sdk carries the Claude CLI; sqlglot decides read from
    write. Both change behaviour under approved versions, so both are exact
    in pyproject and agree with the lock."""
    project = (ROOT / "apps" / "api" / "pyproject.toml").read_text(encoding="utf-8")
    lock = (ROOT / "apps" / "api" / "constraints.txt").read_text(encoding="utf-8")
    for name in ("claude-agent-sdk", "sqlglot"):
        exact = re.search(rf'"{name}==([^"]+)"', project)
        assert exact, f"{name} is pinned exactly in pyproject.toml"
        assert f"{name}=={exact.group(1)}" in lock.splitlines(), f"{name} agrees with the lock"


def test_the_proxy_keeps_an_access_log_for_half_a_year(prod: dict[str, Any]) -> None:
    """Phase 7a.8: who connected from where, for CERT-In's 180 days, on a
    volume (the containers' own logs are gone at the next rebuild), with the
    credentials that travel in addresses taken out."""
    caddy = _without_comments(ROOT / "deploy" / "proxy" / "Caddyfile")
    assert "output file /var/log/caddy/access.log" in caddy
    keep = re.search(r"roll_keep_for (\d+)h", caddy)
    assert keep and int(keep.group(1)) >= 180 * 24
    assert "request>uri regexp" in caddy and "/invites/" in caddy, "invite tokens are cut"
    assert "log_credentials" not in caddy, "Authorization and Cookie stay redacted"
    assert "caddylogs:/var/log/caddy" in prod["services"]["proxy"]["volumes"]
    assert "caddylogs" in prod["volumes"]
