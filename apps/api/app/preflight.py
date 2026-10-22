"""Checks before the server starts (task 6.6): `python -m app.preflight`.

The container's entrypoint runs this first. A deployment that is going to
fail should fail here, in a few lines an operator can read, and not as a
traceback from an import, or an hour later as an error in someone's chat.

Three kinds of finding:

- **FAIL**: the server cannot do its job (a missing or default secret, no
  database, a key that does not open the stored secrets). Exit code 1.
- **WARN**: it will run, and something is probably not what was intended
  (open sign-up, a localhost address in a production setting).
- **ok**.

`/readyz` repeats the reachability checks for as long as the server runs.
This adds what only makes sense once, at the start: reading the settings
as a whole, and trying the encryption key against what is stored.

Nothing here prints a secret's value, only its name.
"""

from __future__ import annotations

import asyncio
import ipaddress
import sys
import warnings
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urlsplit

Level = Literal["ok", "warn", "fail", "skip"]

#: Longest any one check may take.
TIMEOUT_S = 10.0


@dataclass
class Finding:
    level: Level
    name: str
    detail: str = ""


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)

    def add(self, level: Level, name: str, detail: str = "") -> None:
        self.findings.append(Finding(level, name, detail))

    @property
    def failed(self) -> bool:
        return any(f.level == "fail" for f in self.findings)

    def render(self) -> str:
        label = {"ok": "ok  ", "warn": "WARN", "fail": "FAIL", "skip": "skip"}
        lines = [
            f"[preflight] {label[f.level]}  {f.name}" + (f": {f.detail}" if f.detail else "")
            for f in self.findings
        ]
        verdict = "cannot start: fix the FAIL lines above" if self.failed else "ready to start"
        return "\n".join([*lines, f"[preflight] {verdict}"])


def load_settings(report: Report):  # type: ignore[no-untyped-def]  # -> Settings | None
    """Import the settings, which is where the production rules are
    enforced (`config.validate_production_secrets`). Their refusal is a
    list of problems: shown as one FAIL line each, without the traceback."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            from app.config import settings
        except RuntimeError as exc:
            for line in str(exc).splitlines()[1:]:
                report.add("fail", "settings", line.strip(" -"))
            return None
        except Exception as exc:  # a pydantic ValidationError: a malformed value
            for line in _validation_lines(exc):
                report.add("fail", "settings", line)
            return None
    for w in caught:
        report.add("warn", "settings", str(w.message))
    report.add("ok", "settings", f"APP_ENV={settings.app_env}")
    return settings


def _validation_lines(exc: Exception) -> list[str]:
    """Which settings were malformed and why, without their values (a bad
    APP_KEK is still a key)."""
    errors = getattr(exc, "errors", None)
    if not callable(errors):
        return [type(exc).__name__]
    return [
        f"{'.'.join(str(p) for p in e.get('loc', ())).upper() or 'settings'}: {e.get('msg', '')}"
        for e in errors(include_input=False, include_context=False)
    ]


def review(settings, report: Report) -> None:  # type: ignore[no-untyped-def]
    """Settings that are valid and probably not meant, in production."""
    if not settings.is_production:
        return
    if settings.org_mode == "multi":
        report.add(
            "warn",
            "ORG_MODE",
            "multi: anyone signed in can create an organisation outside the company's "
            "admins and budgets. Set ORG_MODE=single for an internal install",
        )
    if settings.registration == "open" and settings.org_mode == "multi":
        report.add(
            "warn",
            "REGISTRATION",
            "open: anyone who can reach this server can create an account and use "
            "its model key. Set REGISTRATION=invite unless that is intended",
        )
    local = ("localhost", "127.0.0.1")
    if any(h in settings.app_base_url for h in local):
        report.add("warn", "APP_BASE_URL", "points at localhost: invite links will too")
    if any(h in origin for origin in settings.cors_origin_list for h in local):
        report.add("warn", "CORS_ORIGINS", "allows a localhost origin")
    if settings.database_url.startswith("sqlite"):
        report.add("warn", "DATABASE_URL", "SQLite: no vector search and one writer at a time")
    if not settings.rag_offline and not settings.voyage_api_key:
        report.add(
            "warn",
            "VOYAGE_API_KEY",
            "not set: the knowledge base will use the local model (set RAG_OFFLINE=1 to say so)",
        )
    if not settings.metrics_token:
        report.add("warn", "METRICS_TOKEN", "not set: /metrics is switched off")
    if settings.trusted_proxy_hops == 0:
        report.add(
            "warn",
            "TRUSTED_PROXY_HOPS",
            "0: behind a reverse proxy every request appears to come from the proxy, "
            "so per-address rate limits are shared by everyone",
        )


def review_hosting(settings, report: Report) -> None:  # type: ignore[no-untyped-def]
    """How the server is reached, in production (Phase 8.2, plan D15): an
    internal install gets its certificate through DNS and listens on one
    private address. What the name resolves to is checked by the probe."""
    if not settings.is_production:
        return
    if settings.tls_mode == "public":
        report.add(
            "warn",
            "TLS_MODE",
            "public: the certificate needs ports 80 and 443 open to the internet. For a "
            "server only the office and the VPN reach, use acmedns or cloudflare "
            "(OPERATIONS §2)",
        )
    elif settings.tls_mode == "local":
        report.add(
            "warn", "TLS_MODE", "local: every browser warns about the certificate until trusted"
        )
    bind = settings.bind_ip.strip()
    if bind in ("", "0.0.0.0", "::"):
        report.add(
            "warn",
            "BIND_IP",
            "not set: the proxy listens on every interface. Set the server's office or "
            "Tailscale address, or make sure a firewall closes the others",
        )
    elif _is_global(bind):
        report.add("warn", "BIND_IP", "a public address: the proxy listens to the internet")


def _is_global(address: str) -> bool:
    """A public internet address. Private ranges, loopback and the shared
    range Tailscale uses (100.64.0.0/10) are not."""
    try:
        return ipaddress.ip_address(address).is_global
    except ValueError:
        return False


async def _hosting() -> str:
    """What the site's name resolves to, from here (Phase 8.2). For an
    internal install it should be a private or Tailscale address: a public
    one means people outside can find it."""
    from app.config import settings

    if not settings.is_production:
        return "skip: development"
    host = urlsplit(settings.app_base_url).hostname or ""
    if not host or host in ("localhost", "127.0.0.1"):
        return "skip: no site name"
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, None)
    except OSError as exc:
        raise RuntimeError(f"{host} doesn't resolve from this server: check its DNS") from exc
    addresses = sorted({str(info[4][0]) for info in infos})
    public = [a for a in addresses if _is_global(a)]
    if public and settings.tls_mode != "public":
        raise RuntimeError(
            f"{host} resolves to a public address ({', '.join(public)}). For an internal "
            "install, answer the name with the private address (internal DNS or Tailscale "
            "split DNS) and keep the public zone for the certificate's record only"
        )
    return f"{host} resolves to {', '.join(addresses)}"


def review_two_factor(settings, report: Report) -> None:  # type: ignore[no-untyped-def]
    """Admins and a second factor, in production (Phase 8.4)."""
    if not settings.is_production:
        return
    if not settings.require_admin_2fa:
        report.add(
            "warn",
            "REQUIRE_ADMIN_2FA",
            "off: an admin's password alone opens every conversation and every approval. "
            "Turn it on",
        )
    if settings.trusted_internal_nets:
        try:
            from app.api.ratelimit import _internal_nets

            _internal_nets(settings.trusted_internal_nets)
        except ValueError:
            report.add("fail", "TRUSTED_INTERNAL_NETS", "not a list of address ranges (CIDRs)")


def review_privacy(settings, report: Report) -> None:  # type: ignore[no-untyped-def]
    """The privacy notice before the first chat, in production (Phase 8.7)."""
    if settings.is_production and not settings.privacy_notice_required:
        report.add(
            "warn",
            "PRIVACY_NOTICE_REQUIRED",
            "off: people can chat without being told that admins can read conversations "
            "or where messages go. Turn it on",
        )


def review_tracing(settings, report: Report) -> None:  # type: ignore[no-untyped-def]
    """Tracing in production (Phase 8.10): full content is a second copy of
    every conversation that erasure and retention don't reach."""
    exporting = bool(settings.langfuse_host or settings.otel_exporter_otlp_endpoint)
    if settings.is_production and exporting and settings.trace_full:
        report.add(
            "warn",
            "TRACE_CONTENT",
            "full: traces carry every message and answer, a copy that erasing a person or "
            "the retention rule doesn't reach. Use metadata unless you need it",
        )


def review_google(settings, report: Report) -> None:  # type: ignore[no-untyped-def]
    """Google sign-in (Phase 8.3): set up whole, and limited to the company's
    domain in production. Never prints a value."""
    has_id, has_secret = bool(settings.google_client_id), bool(settings.google_client_secret)
    if has_id != has_secret:
        report.add(
            "fail",
            "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET",
            "only one is set: Google sign-in needs both",
        )
        return
    if not has_id or not settings.is_production:
        return
    if not settings.google_allowed_domain:
        report.add(
            "warn",
            "GOOGLE_ALLOWED_DOMAIN",
            "not set: any Google account can be used with an invite, and auto-join is off. "
            "Set the company's Google Workspace domain",
        )
    if not settings.app_base_url.startswith("https://"):
        report.add("warn", "APP_BASE_URL", "Google sends people back only to an https address")


def review_mail(settings, report: Report) -> None:  # type: ignore[no-untyped-def]
    """Email in production (Phase 7b.5): configured to work, and not through
    a mailbox that would put the company at risk. Never prints a value."""
    if not settings.is_production:
        return
    transport = settings.mail_transport
    if transport in ("console", "memory"):
        report.add(
            "fail",
            "MAIL_BACKEND",
            f"{transport} in production: messages, invite links included, would be logged or "
            "kept in memory. Use smtp, or off",
        )
        return
    if transport == "off":
        report.add(
            "warn",
            "SMTP_HOST",
            "not set: no email is sent. Invite links are copied by hand, and nobody is told "
            "when a version waits for review",
        )
        return
    if not (settings.mail_from or settings.smtp_username):
        report.add("fail", "MAIL_FROM", "not set: there is no address to send from")
    if settings.smtp_username and not settings.smtp_password:
        report.add("fail", "SMTP_PASSWORD", "not set, although SMTP_USERNAME is")
    if settings.smtp_security == "none" and settings.smtp_host not in ("localhost", "127.0.0.1"):
        report.add(
            "warn",
            "SMTP_SECURITY",
            "none to another host: the password and every message cross the network in clear",
        )
    personal = [
        name
        for name, value in (
            ("MAIL_FROM", settings.mail_from),
            ("SMTP_USERNAME", settings.smtp_username),
        )
        if value.lower().endswith(("@gmail.com", "@googlemail.com"))
    ]
    if personal:
        report.add(
            "warn",
            " / ".join(personal),
            "a personal gmail.com account: its app password opens the whole account, mail "
            "included, and changing its password stops all email. Prefer a company mailbox "
            "or a send-only address",
        )


def review_models(settings, report: Report) -> None:  # type: ignore[no-untyped-def]
    """MODEL_ALIASES may only repoint a known alias at a pinned model id
    (Phase 7a.6): a typo would send every assistant on that alias to a model
    the API does not know."""
    from app.agent.models import MODEL_ALIASES, PINNED_MODELS

    for alias, target in settings.model_aliases.items():
        if alias not in MODEL_ALIASES:
            report.add(
                "fail",
                "MODEL_ALIASES",
                f"{alias!r} is not an alias; the aliases are {sorted(MODEL_ALIASES)}",
            )
        elif target not in PINNED_MODELS:
            report.add(
                "fail",
                "MODEL_ALIASES",
                f"{alias!r} points at {target!r}, which is not a model this release knows",
            )


def _models_named(config: dict[str, Any]) -> set[str]:
    """Every model name in a stored config: the four roles and each
    subagent's own."""
    roles = config.get("models") or {}
    own = (config.get("subagents") or {}).get("models") or {}
    return {
        spec["model"]
        for spec in [*roles.values(), *own.values()]
        if isinstance(spec, dict) and isinstance(spec.get("model"), str)
    }


async def _models_in_use() -> str:
    """Saved versions that pin a model on its way out (Phase 7a.6). Not a
    failure: a deprecated model still answers, and a retired one runs as its
    replacement. It is the operator's cue to move those assistants."""
    from sqlalchemy import select
    from sqlalchemy.exc import DBAPIError, OperationalError, ProgrammingError

    from app.agent.models import DEPRECATED_MODELS, RETIRED_MODELS
    from app.db.session import get_sessionmaker
    from app.models.assistant import AssistantVersion

    try:
        async with get_sessionmaker()() as session:
            configs = (await session.scalars(select(AssistantVersion.config))).all()
    except (ProgrammingError, OperationalError, DBAPIError):
        return "skip: no versions table yet (first start)"
    named: dict[str, int] = {}
    for config in configs:
        for model in _models_named(config):
            if model in DEPRECATED_MODELS or model in RETIRED_MODELS:
                named[model] = named.get(model, 0) + 1
    if not named:
        return ""
    parts = [
        f"{model} in {count} version(s), "
        + (
            f"retired, running as {RETIRED_MODELS[model]}"
            if model in RETIRED_MODELS
            else f"retires {DEPRECATED_MODELS[model]}"
        )
        for model, count in sorted(named.items())
    ]
    raise RuntimeError("; ".join(parts) + ". Move them to an alias such as 'sonnet'")


async def _database() -> str:
    from sqlalchemy import text

    from app.db.session import get_engine

    async with get_engine().connect() as conn:
        await conn.execute(text("SELECT 1"))
    return ""


async def _redis() -> str:
    from app.db.redis import get_redis

    await get_redis().ping()
    return ""


async def _storage() -> str:
    from app.storage import probe_bucket

    await probe_bucket()
    return ""


async def _agent_cli() -> str:
    from app.agent.driver import ClaudeSDKDriver, get_driver
    from app.api.routes.health import claude_cli_path

    if not isinstance(get_driver(), ClaudeSDKDriver):
        return "skip: offline driver (AGENT_DRIVER=fake, or no ANTHROPIC_API_KEY)"
    if not claude_cli_path():
        raise RuntimeError("the Claude CLI was not found in the image")
    return ""


async def _encryption_key() -> str:
    """The key must open what is already stored. After a restore, or a
    changed APP_KEK, it does not, and every database connection and MCP
    credential fails at the moment someone uses it."""
    from sqlalchemy import select
    from sqlalchemy.exc import DBAPIError, OperationalError, ProgrammingError

    from app.config import settings
    from app.db.session import get_sessionmaker
    from app.models.secret import Secret
    from app.security.crypto import open_sealed, seal
    from app.security.rotation import _opens_with

    if not settings.app_kek:
        return "skip: APP_KEK not set (stored credentials are unavailable)"
    # The key itself works: seal and open with it, and not with another.
    sealed = seal("preflight", kind="preflight")
    if open_sealed(sealed, kind="preflight") != "preflight":
        raise RuntimeError("APP_KEK does not round-trip")
    try:
        async with get_sessionmaker()() as session:
            row = await session.scalar(select(Secret).limit(1))
    except (ProgrammingError, OperationalError, DBAPIError):
        return "skip: no secrets table yet (first start)"
    if row is None:
        return "nothing stored yet"
    if not _opens_with(row, settings.app_kek):
        raise RuntimeError(
            "APP_KEK does not open the stored credentials. It is not the key they were "
            "saved with: restore the old key, or rotate with `python -m scripts.rotate_kek`"
        )
    return "opens the stored credentials"


#: name -> (check, is a failure fatal)
CHECKS: dict[str, tuple[Callable[[], Awaitable[str]], bool]] = {
    "database": (_database, True),
    "encryption key": (_encryption_key, True),
    "agent CLI": (_agent_cli, True),
    "redis": (_redis, False),
    "object storage": (_storage, False),
    "models in use": (_models_in_use, False),
    "site name": (_hosting, False),
}


async def probe(report: Report) -> None:
    for name, (check, fatal) in CHECKS.items():
        try:
            detail = await asyncio.wait_for(check(), TIMEOUT_S)
        except TimeoutError:
            report.add("fail" if fatal else "warn", name, f"no answer in {TIMEOUT_S:g} s")
        except Exception as exc:
            # The type, and a message only for our own errors: a driver's
            # message can quote the connection string.
            said = str(exc) if type(exc) is RuntimeError else type(exc).__name__
            report.add("fail" if fatal else "warn", name, said)
        else:
            if detail.startswith("skip: "):
                report.add("skip", name, detail[6:])
            else:
                report.add("ok", name, detail)


async def run() -> Report:
    report = Report()
    settings = load_settings(report)
    if settings is None:
        return report
    review(settings, report)
    review_hosting(settings, report)
    review_google(settings, report)
    review_two_factor(settings, report)
    review_privacy(settings, report)
    review_tracing(settings, report)
    review_models(settings, report)
    review_mail(settings, report)
    await probe(report)
    return report


def main() -> int:
    report = asyncio.run(run())
    print(report.render(), flush=True)
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
