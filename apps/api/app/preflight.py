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
import sys
import warnings
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal

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
    if settings.registration == "open":
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
    await probe(report)
    return report


def main() -> int:
    report = asyncio.run(run())
    print(report.render(), flush=True)
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
