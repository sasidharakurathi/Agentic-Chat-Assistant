"""Talking to a registered MCP server: health checks and tool discovery (task 4.5).

`connect()` opens an MCP client session to any registered server:

- **stdio**: never started here. The runner starts it (sandboxed, with its
  sealed environment) and the session goes to the runner's URL.
- **http / sse**: with its sealed headers, through `pinned_client`: every
  request's host must resolve to public addresses and is pinned to the
  address checked, and redirects are never followed. The SDK's own default
  client follows redirects, which would be a way around the check.

`check()` proves the server answers the MCP handshake. `discover()` also
lists its tools and stores them on the row. Both record the outcome
(`status`, `error`, `last_checked_at`) so the MCP tab shows it.

What a server says about its tools is **untrusted**: names, descriptions
and schemas come from someone else's code, and a description is text the
model will read. The catalog is validated and capped here, and shown in
full to the builder before any tool is allowed (task 4.10).
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import anyio
import httpx2
from mcp.client.session import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.config import settings
from app.logging import get_logger
from app.models.integration import McpServer, McpServerStatus, McpTransport
from app.schemas.mcp_server import McpCheckResult
from app.security.pinned_transport import pinned_client
from app.security.redact import strip_secrets
from app.security.ssrf import SsrfBlocked
from app.services import mcp_runner
from app.services.mcp_servers import open_env, open_headers

log = get_logger(__name__)

#: Starting a stdio server may mean `npx` fetching a package first.
STDIO_TIMEOUT_S = 90.0
REMOTE_TIMEOUT_S = 30.0

#: What a model's tool name may contain (and how long), so the qualified
#: `mcp__<server>__<tool>` name stays valid everywhere it is used.
TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_TOOLS = 200
MAX_PAGES = 20
MAX_DESCRIPTION = 2_000
MAX_SCHEMA_BYTES = 32_000


class McpUnavailable(Exception):
    """The server could not be reached or refused; the message says why, for
    the person looking at the MCP tab."""


def _timeout(server: McpServer) -> float:
    return STDIO_TIMEOUT_S if server.transport == McpTransport.stdio else REMOTE_TIMEOUT_S


def _remote_client(
    headers: dict[str, str] | None = None,
    timeout: httpx2.Timeout | None = None,
    auth: httpx2.Auth | None = None,
) -> httpx2.AsyncClient:
    """Matches the SDK's client-factory signature, so `sse_client` builds its
    client this way too instead of its own redirect-following default."""
    local = settings.mcp_allow_insecure_urls
    return pinned_client(
        headers=headers,
        timeout=timeout,
        auth=auth,
        allow_insecure=local,
        allow_private=local,
    )


async def load_secrets(server_id: Any) -> tuple[dict[str, str], dict[str, str]]:
    """A server's decrypted environment and headers, read in a short-lived
    session of their own, so nothing holds a database connection for as long
    as an MCP connection stays open."""
    from app.db.session import get_sessionmaker

    async with get_sessionmaker()() as db:
        server = await db.get(McpServer, server_id)
        if server is None:
            raise McpUnavailable("the server is no longer registered")
        return await open_env(db, server), await open_headers(db, server)


@asynccontextmanager
async def connect(db: AsyncSession, server: McpServer) -> AsyncIterator[ClientSession]:
    """An initialized MCP client session to `server`, closed on exit."""
    env = await open_env(db, server) if server.transport == McpTransport.stdio else {}
    headers = await open_headers(db, server) if server.transport != McpTransport.stdio else {}
    async with connect_with(server, env=env, headers=headers) as session:
        yield session


@asynccontextmanager
async def connect_with(
    server: mcp_runner.ServerLike, *, env: dict[str, str], headers: dict[str, str]
) -> AsyncIterator[ClientSession]:
    """`connect`, with the secrets already opened: what a chat turn uses, so
    it can read them once and keep no database session for the connection's
    lifetime."""
    if server.transport == McpTransport.stdio:
        try:
            run = await mcp_runner.open_session(server, env)
        except mcp_runner.RunnerUnavailable as exc:
            raise McpUnavailable(str(exc)) from exc
        client = httpx2.AsyncClient(
            headers={"Authorization": f"Bearer {run.token}"},
            timeout=httpx2.Timeout(30.0, read=300.0),
            follow_redirects=False,
            trust_env=False,
        )
        try:
            async with (
                streamable_http_client(run.url, http_client=client) as (read, write),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                yield session
        finally:
            await mcp_runner.close_session(run.session_id)
        return

    url = server.url or ""
    if server.transport == McpTransport.sse:
        async with (
            sse_client(url, headers=headers, httpx_client_factory=_remote_client) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            yield session
        return
    async with (
        streamable_http_client(url, http_client=_remote_client(headers)) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        yield session


# ── errors ───────────────────────────────────────────────────


def _leaves(exc: BaseException) -> list[BaseException]:
    """The real errors inside the exception groups task groups raise."""
    if isinstance(exc, BaseExceptionGroup):
        out: list[BaseException] = []
        for inner in exc.exceptions:
            out.extend(_leaves(inner))
        return out
    return [exc]


def _http_status(code: int) -> str:
    if code in (401, 403):
        return f"The server refused the credentials (HTTP {code}). Check the headers."
    if code in (301, 302, 303, 307, 308):
        return (
            f"The server redirected (HTTP {code}). Redirects are not followed; "
            "register the final URL instead."
        )
    return f"The server answered HTTP {code}."


def _known(leaf: BaseException) -> str | None:
    """A message for the errors worth naming; None for anything else."""
    message: str | None = None
    if isinstance(leaf, McpUnavailable | SsrfBlocked):
        message = str(leaf)
    elif isinstance(leaf, TimeoutError):
        message = "The server did not answer in time."
    elif isinstance(leaf, httpx2.HTTPStatusError):
        message = _http_status(leaf.response.status_code)
    elif isinstance(leaf, httpx2.ConnectError | httpx2.ConnectTimeout):
        message = "Could not connect to the server."
    elif isinstance(leaf, AppError):
        message = leaf.message
    return message


def describe(exc: BaseException) -> str:
    """One sentence a builder can act on, without internal detail or secrets."""
    leaves = _leaves(exc)
    for leaf in leaves:
        known = _known(leaf)
        if known is not None:
            return known
    first = leaves[0]
    text = strip_secrets(str(first))[:300]
    return f"The server could not be used: {type(first).__name__}" + (f": {text}" if text else "")


# ── the catalog ──────────────────────────────────────────────


def normalize_tools(tools: list[Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """Validated, capped tool records, and a note for each tool left out."""
    kept: list[dict[str, Any]] = []
    skipped: list[str] = []
    seen: set[str] = set()
    for tool in tools:
        name = str(getattr(tool, "name", "") or "")
        if not TOOL_NAME_RE.fullmatch(name):
            skipped.append(f"{name[:80]!r}: names may use letters, digits, _ and - (up to 64)")
            continue
        if "__" in name:
            # `__` is how a tool's full name is put together
            # (`mcp__server__tool`). A tool called `mcp__caps__sql_query`
            # could pass for a platform tool on an approval card.
            skipped.append(f"{name[:80]!r}: names may not contain a double underscore")
            continue
        if name in seen:
            skipped.append(f"{name}: listed twice")
            continue
        if len(kept) >= MAX_TOOLS:
            skipped.append(f"{name}: more than {MAX_TOOLS} tools")
            continue
        schema = getattr(tool, "input_schema", None) or {"type": "object"}
        if len(json.dumps(schema, default=str)) > MAX_SCHEMA_BYTES:
            skipped.append(f"{name}: its input schema is larger than {MAX_SCHEMA_BYTES:,} bytes")
            continue
        annotations = getattr(tool, "annotations", None)
        seen.add(name)
        kept.append(
            {
                "name": name,
                "description": str(getattr(tool, "description", "") or "")[:MAX_DESCRIPTION],
                "input_schema": schema,
                "read_only": getattr(annotations, "read_only_hint", None),
            }
        )
    return kept, skipped


async def _list_all(session: ClientSession) -> list[Any]:
    from mcp_types import PaginatedRequestParams

    tools: list[Any] = []
    cursor: str | None = None
    for _ in range(MAX_PAGES):
        params = PaginatedRequestParams(cursor=cursor) if cursor else None
        page = await session.list_tools(params=params)
        tools.extend(page.tools)
        cursor = page.next_cursor
        if not cursor or len(tools) > MAX_TOOLS:
            break
    return tools


# ── the two operations ───────────────────────────────────────


def _record(server: McpServer, *, ok: bool, error: str | None) -> None:
    server.status = McpServerStatus.ok if ok else McpServerStatus.error
    server.error = error
    server.last_checked_at = datetime.now(UTC)


async def check(db: AsyncSession, server: McpServer) -> McpCheckResult:
    """Can the server be reached, and does it complete the MCP handshake?"""
    started = time.monotonic()
    try:
        with anyio.fail_after(_timeout(server)):
            async with connect(db, server):
                pass
    except Exception as exc:
        message = describe(exc)
        log.info("mcp_check_failed", server=str(server.id), error=message)
        _record(server, ok=False, error=message)
        await db.commit()
        return McpCheckResult(ok=False, error=message, elapsed_ms=_ms(started))
    _record(server, ok=True, error=None)
    await db.commit()
    return McpCheckResult(ok=True, elapsed_ms=_ms(started))


async def discover(db: AsyncSession, server: McpServer) -> McpCheckResult:
    """Connect, list the tools, and store them. A failure keeps the previous
    list: a server that is down for a minute still offers what it offered."""
    started = time.monotonic()
    try:
        with anyio.fail_after(_timeout(server)):
            async with connect(db, server) as session:
                listed = await _list_all(session)
    except Exception as exc:
        message = describe(exc)
        log.info("mcp_discover_failed", server=str(server.id), error=message)
        _record(server, ok=False, error=message)
        await db.commit()
        return McpCheckResult(ok=False, error=message, elapsed_ms=_ms(started))

    tools, skipped = normalize_tools(listed)
    server.tools = tools
    server.tools_discovered_at = datetime.now(UTC)
    _record(server, ok=True, error=None)
    await db.commit()
    log.info("mcp_discovered", server=str(server.id), tools=len(tools), skipped=len(skipped))
    return McpCheckResult(ok=True, elapsed_ms=_ms(started), tool_count=len(tools), skipped=skipped)


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


__all__ = [
    "McpUnavailable",
    "check",
    "connect",
    "connect_with",
    "describe",
    "discover",
    "load_secrets",
    "normalize_tools",
]
