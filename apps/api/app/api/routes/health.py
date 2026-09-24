from __future__ import annotations

import asyncio
import platform
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import APIRouter, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import __version__
from app.agent.driver import ClaudeSDKDriver, get_driver
from app.api.deps import SessionDep
from app.db.redis import get_redis
from app.logging import get_logger
from app.schemas.common import HealthResponse, ReadyResponse
from app.storage import probe_bucket

router = APIRouter(tags=["health"])
log = get_logger(__name__)

#: Longest any one dependency may take to answer before it counts as down.
CHECK_TIMEOUT_S = 2.0

#: Without these the instance cannot serve a turn at all: 503, so a load
#: balancer stops sending it traffic. The others degrade features (indexing,
#: uploads, cross-worker stop and approvals) but chat still works, and failing
#: readiness on them would pull every instance out at once during a Redis or
#: storage outage, turning a degraded service into none.
CRITICAL = frozenset({"database", "agent_cli"})


@router.get("/healthz", response_model=HealthResponse)
async def healthz() -> HealthResponse:
    """Liveness: the process is up. No dependency checks."""
    return HealthResponse(status="ok", version=__version__)


def claude_cli_path() -> str | None:
    """Where the Agent SDK will find its CLI, by the SDK's own rule: the
    binary bundled in the package, else `claude` on PATH."""
    import claude_agent_sdk

    name = "claude.exe" if platform.system() == "Windows" else "claude"
    bundled = Path(claude_agent_sdk.__file__).parent / "_bundled" / name
    if bundled.is_file():
        return str(bundled)
    return shutil.which("claude")


async def _database(session: AsyncSession) -> str:
    await session.execute(text("SELECT 1"))
    return "ok"


async def _redis() -> str:
    await get_redis().ping()
    return "ok"


async def _storage() -> str:
    await probe_bucket()
    return "ok"


async def _agent_cli() -> str:
    if not isinstance(get_driver(), ClaudeSDKDriver):
        return "skipped: offline driver"
    return "ok" if claude_cli_path() else "error: Claude CLI not found"


async def _run(name: str, check: Callable[[], Awaitable[str]]) -> tuple[str, str]:
    try:
        return name, await asyncio.wait_for(check(), CHECK_TIMEOUT_S)
    except TimeoutError:
        result = "error: timed out"
    except Exception as exc:
        result = f"error: {exc.__class__.__name__}"
    log.warning("readiness_check_failed", check=name, result=result)
    return name, result


@router.get("/readyz", response_model=ReadyResponse)
async def readyz(session: SessionDep, response: Response) -> ReadyResponse:
    """Readiness: database, Redis, object storage and (when the real driver
    would run) the Claude CLI, checked concurrently, each within
    `CHECK_TIMEOUT_S`. It used to check the database only.

    503 when a critical check fails; 200 "degraded" when only a non-critical
    one does; 200 "ok" otherwise. Each check's result is in `checks`."""
    results = await asyncio.gather(
        _run("database", lambda: _database(session)),
        _run("redis", _redis),
        _run("storage", _storage),
        _run("agent_cli", _agent_cli),
    )
    checks = dict(results)
    failed = {name for name, value in checks.items() if value.startswith("error")}
    if failed & CRITICAL:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadyResponse(status="unavailable", checks=checks)
    response.status_code = status.HTTP_200_OK
    return ReadyResponse(status="degraded" if failed else "ok", checks=checks)
