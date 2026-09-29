"""The API's side of the MCP runner (task 4.4; see `app/mcp/runner.py`).

A stdio server is never started here. The API asks the runner to start one,
gets back a URL and a per-session token, and from then on talks to it like
any remote MCP server: the agent SDK (task 4.6) and tool discovery (4.5)
connect to that URL.

The runner is platform infrastructure at a configured address, so the SSRF
guard (which is for URLs tenants choose) does not apply to it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.config import settings
from app.logging import get_logger
from app.mcp.limits import SandboxLimits

log = get_logger(__name__)

#: Starting a server can mean `npx` fetching a package on first use.
START_TIMEOUT_S = 60.0
CALL_TIMEOUT_S = 10.0


class ServerLike(Protocol):
    """What starting or reaching a server needs: an `McpServer` row, or the
    snapshot a chat turn takes of one."""

    @property
    def name(self) -> str: ...
    @property
    def transport(self) -> Any: ...
    @property
    def command(self) -> str | None: ...
    @property
    def args(self) -> list[str]: ...
    @property
    def url(self) -> str | None: ...
    @property
    def sandbox(self) -> dict[str, Any]: ...


class RunnerUnavailable(Exception):
    """The runner could not be reached or refused. The message is written
    for the person looking at the server's status."""


@dataclass(frozen=True)
class RunnerSession:
    session_id: str
    url: str
    token: str
    #: What the runner says it enforces: platform, network, applied limits.
    enforced: dict[str, Any]

    def sdk_config(self) -> dict[str, Any]:
        """How the agent SDK reaches this server (an MCP HTTP server config)."""
        return {
            "type": "http",
            "url": self.url,
            "headers": {"Authorization": f"Bearer {self.token}"},
        }


def _base() -> str:
    return settings.mcp_runner_url.rstrip("/")


def _headers() -> dict[str, str]:
    token = settings.mcp_runner_token_effective
    if not token:
        raise RunnerUnavailable(
            "MCP_RUNNER_TOKEN is not set, so local-command MCP servers cannot run here."
        )
    return {"Authorization": f"Bearer {token}"}


def _unreachable(exc: Exception) -> RunnerUnavailable:
    log.warning("mcp_runner_unreachable", url=_base(), error=type(exc).__name__)
    return RunnerUnavailable(
        f"The MCP runner is not reachable at {_base()}. Local-command servers run there: "
        "start it with scripts/dev-mcp-runner.ps1, or the mcp-runner container."
    )


async def open_session(
    server: ServerLike, env: dict[str, str], *, client: httpx.AsyncClient | None = None
) -> RunnerSession:
    """Start `server` (a stdio registration) on the runner."""
    body = {
        "command": server.command,
        "args": list(server.args or []),
        "env": env,
        "limits": SandboxLimits.from_row(server.sandbox).model_dump(),
        "label": server.name,
    }
    try:
        async with _client(client, START_TIMEOUT_S) as http:
            resp = await http.post(f"{_base()}/sessions", json=body, headers=_headers())
    except httpx.HTTPError as exc:
        raise _unreachable(exc) from exc
    if resp.status_code != 201:  # noqa: PLR2004
        raise RunnerUnavailable(_error_of(resp, "the runner could not start the server"))
    data = resp.json()
    return RunnerSession(
        session_id=data["session_id"],
        url=f"{_base()}{data['path']}",
        token=data["token"],
        enforced=data.get("enforced", {}),
    )


async def close_session(session_id: str, *, client: httpx.AsyncClient | None = None) -> None:
    """Best effort: the runner's idle timeout stops it anyway."""
    try:
        async with _client(client, CALL_TIMEOUT_S) as http:
            await http.delete(f"{_base()}/sessions/{session_id}", headers=_headers())
    except (httpx.HTTPError, RunnerUnavailable) as exc:
        log.info("mcp_runner_close_failed", session=session_id, error=type(exc).__name__)


async def session_status(
    session_id: str, *, client: httpx.AsyncClient | None = None
) -> dict[str, Any]:
    try:
        async with _client(client, CALL_TIMEOUT_S) as http:
            resp = await http.get(f"{_base()}/sessions/{session_id}", headers=_headers())
    except httpx.HTTPError as exc:
        raise _unreachable(exc) from exc
    return dict(resp.json()) if resp.status_code == 200 else {"running": False}  # noqa: PLR2004


async def health(*, client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    if not settings.mcp_runner_url:
        raise RunnerUnavailable("No MCP runner is configured (MCP_RUNNER_URL is empty).")
    try:
        async with _client(client, CALL_TIMEOUT_S) as http:
            resp = await http.get(f"{_base()}/healthz")
    except httpx.HTTPError as exc:
        raise _unreachable(exc) from exc
    return dict(resp.json())


def _error_of(resp: httpx.Response, default: str) -> str:
    try:
        return str(resp.json().get("error") or default)
    except ValueError:
        return default


class _client:
    """Use the caller's client (tests) or a short-lived one."""

    def __init__(self, given: httpx.AsyncClient | None, timeout: float) -> None:
        self._given = given
        self._own: httpx.AsyncClient | None = None
        self._timeout = timeout

    async def __aenter__(self) -> httpx.AsyncClient:
        if self._given is not None:
            return self._given
        self._own = httpx.AsyncClient(timeout=self._timeout, trust_env=False)
        return self._own

    async def __aexit__(self, *exc: object) -> None:
        if self._own is not None:
            await self._own.aclose()


__all__ = [
    "RunnerSession",
    "RunnerUnavailable",
    "ServerLike",
    "close_session",
    "health",
    "open_session",
    "session_status",
]
