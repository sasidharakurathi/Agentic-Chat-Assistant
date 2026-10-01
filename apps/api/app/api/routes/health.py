from __future__ import annotations

import asyncio
import hmac
import platform
import shutil
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import __version__
from app.agent.driver import ClaudeSDKDriver, get_driver
from app.api.deps import SessionDep
from app.config import settings
from app.db.redis import get_redis
from app.logging import get_logger
from app.observability import collect, metrics
from app.schemas.common import HealthResponse, ReadyResponse
from app.services import mcp_runner
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


@router.get("/metrics", include_in_schema=False)
async def prometheus_metrics(request: Request) -> Response:
    """Prometheus's text format (task 6.4): what this process observed, and
    what the database and the queue say right now.

    The page names orgs and assistants and says what each spends, so it is
    not public: with `METRICS_TOKEN` set it needs that as a bearer token.
    Without one it is open in development and absent in production.
    """
    expected = settings.metrics_token
    if expected:
        given = request.headers.get("authorization", "")
        if not hmac.compare_digest(given.encode(), f"Bearer {expected}".encode()):
            return Response(
                "metrics need a bearer token\n",
                status_code=status.HTTP_401_UNAUTHORIZED,
                headers={"WWW-Authenticate": "Bearer"},
                media_type="text/plain",
            )
    elif settings.is_production:
        return Response(
            "not found\n", status_code=status.HTTP_404_NOT_FOUND, media_type="text/plain"
        )
    body = metrics.render(await collect.collect())
    return Response(body, media_type="text/plain; version=0.0.4; charset=utf-8")


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


def loop_can_spawn() -> bool:
    """Whether the running event loop can start subprocesses. On Windows only
    the Proactor loop can, and uvicorn picks the Selector loop under
    `--reload` (see `app/devserver.py` for why dev-api.ps1 does not use it)."""
    # `sys.platform`, not `platform.system()`: the type checker understands
    # this check, so the Windows-only class type-checks on Linux too.
    if sys.platform != "win32":
        return True
    return isinstance(asyncio.get_running_loop(), asyncio.ProactorEventLoop)


async def _agent_cli() -> str:
    if not isinstance(get_driver(), ClaudeSDKDriver):
        return "skipped: offline driver"
    if not claude_cli_path():
        return "error: Claude CLI not found"
    if not loop_can_spawn():
        return (
            "error: this server's event loop cannot start the Claude CLI; "
            "run uvicorn without --reload (scripts/dev-api.ps1 restarts it on changes)"
        )
    return "ok"


async def _mcp_runner() -> str:
    """Local-command MCP servers run in the runner (task 4.4). Not critical:
    without it only those servers are unavailable."""
    if not settings.mcp_runner_url:
        return "skipped: not configured"
    await mcp_runner.health()
    return "ok"


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
        _run("mcp_runner", _mcp_runner),
    )
    checks = dict(results)
    failed = {name for name, value in checks.items() if value.startswith("error")}
    if failed & CRITICAL:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadyResponse(status="unavailable", checks=checks)
    response.status_code = status.HTTP_200_OK
    return ReadyResponse(status="degraded" if failed else "ok", checks=checks)
