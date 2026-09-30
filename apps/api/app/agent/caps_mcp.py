"""Tools from registered MCP servers, as the agent sees them (task 4.6).

The Claude CLI can connect to MCP servers by itself: give it a URL and it
does the rest. That is exactly what this module avoids. The CLI would make
those connections from inside the API's process, with none of the
platform's protections: no SSRF guard or pinning for a remote server, and a
stdio server would run right here instead of in the sandboxed runner. Its
results would also reach the model without the post-tool step (the size cap
and secret stripping every other tool gets).

So each allowed tool becomes a platform capability instead, in an
in-process SDK server named after its MCP server, and the model sees it as
`mcp__<server>__<tool>`. Calling it forwards the call over the connection
`services/mcp_discovery.connect_with` opens: through the runner for a local
command, through the pinned client for a remote server. Everything the
platform does to a tool call (the PreToolUse gate, approvals, the post-tool
step, the audit row) applies unchanged.

**Which tools.** Only those in the assistant version's allowlist
(`mcp_servers[].tools`, chosen on the canvas node) *and* in the server's
discovered catalog, from enabled servers registered on this assistant. A
tool the server no longer lists is not offered, whatever the allowlist says.

**Connections.** One per server per turn, opened on the first call (a turn
that uses no MCP tool starts no server) and closed when the turn ends.
Each is owned by a background task, because the MCP client's context must
be entered and exited in the same task, while tool calls arrive on whatever
task the SDK runs them in.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from mcp.client.session import ClientSession
from sqlalchemy import select

from app.agent.approvals import mcp_mode
from app.agent.caps import CapabilityTool
from app.config import settings
from app.db.session import get_sessionmaker
from app.logging import get_logger
from app.models.integration import McpServer
from app.schemas.assistant_config import ApprovalMode, AssistantConfig, McpServerRef
from app.services.mcp_discovery import McpUnavailable, connect_with, describe, load_secrets

log = get_logger(__name__)

#: How long a server may take to start and complete the handshake.
CONNECT_TIMEOUT_S = 90.0
#: How much of a non-text result is described instead of shown.
_BLOB_NOTE = "[{kind} content not shown]"
_EMPTY_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}}


@dataclass(frozen=True)
class ServerSnapshot:
    """What a turn needs to know about a server, read once at its start."""

    id: uuid.UUID
    name: str
    transport: str
    command: str | None
    args: list[str]
    url: str | None
    sandbox: dict[str, Any]


class _Connection:
    def __init__(self, server: ServerSnapshot) -> None:
        self.server = server
        self.session: ClientSession | None = None
        self.error: str | None = None
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def get(self) -> ClientSession:
        if self._task is None:
            self._task = asyncio.create_task(self._run())
        try:
            await asyncio.wait_for(self._ready.wait(), CONNECT_TIMEOUT_S)
        except TimeoutError:
            self.error = "the server did not start in time"
            await self.aclose()
        if self.session is None:
            raise McpUnavailable(self.error or "the server is not available")
        return self.session

    async def _run(self) -> None:
        try:
            env, headers = await load_secrets(self.server.id)
            async with connect_with(self.server, env=env, headers=headers) as session:
                self.session = session
                self._ready.set()
                await self._stop.wait()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.error = describe(exc)
            log.info("mcp_connection_failed", server=self.server.name, error=self.error)
        finally:
            self.session = None
            self._ready.set()

    async def aclose(self) -> None:
        self._stop.set()
        task = self._task
        if task is not None and not task.done():
            with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError, Exception):
                await asyncio.wait_for(task, 15)


@dataclass
class McpToolset:
    """This turn's MCP tools and the connections behind them."""

    tools: list[CapabilityTool] = field(default_factory=list)
    #: qualified tool name -> its approval rule (task 4.7), for the router.
    modes: dict[str, ApprovalMode] = field(default_factory=dict)
    _connections: dict[uuid.UUID, _Connection] = field(default_factory=dict)

    def connection(self, server: ServerSnapshot) -> _Connection:
        if server.id not in self._connections:
            self._connections[server.id] = _Connection(server)
        return self._connections[server.id]

    async def aclose(self) -> None:
        for conn in list(self._connections.values()):
            await conn.aclose()
        self._connections.clear()


def render_result(result: Any) -> dict[str, Any]:
    """An MCP `CallToolResult` as a capability result: text blocks kept,
    anything else described, and the error flag carried over."""
    parts: list[str] = []
    for block in getattr(result, "content", None) or []:
        kind = str(getattr(block, "type", "content"))
        text = getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
        elif kind == "resource" and isinstance(
            getattr(getattr(block, "resource", None), "text", None), str
        ):
            parts.append(block.resource.text)
        else:
            parts.append(_BLOB_NOTE.format(kind=kind))
    structured = getattr(result, "structured_content", None)
    if not parts and structured is not None:
        parts.append(json.dumps(structured, indent=2, ensure_ascii=False, default=str))
    out: dict[str, Any] = {
        "content": [{"type": "text", "text": "\n".join(parts) or "(no content)"}]
    }
    if getattr(result, "is_error", False):
        out["is_error"] = True
    return out


def _err(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "is_error": True}


def _proxy(toolset: McpToolset, server: ServerSnapshot, tool: str) -> Any:
    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        label = f"{server.name}.{tool}"
        try:
            session = await toolset.connection(server).get()
            result = await asyncio.wait_for(
                session.call_tool(tool, args), settings.mcp_call_timeout_s
            )
        except McpUnavailable as exc:
            return _err(f"The MCP server '{server.name}' is unavailable: {exc}")
        except TimeoutError:
            return _err(f"{label} did not answer within {settings.mcp_call_timeout_s:g}s.")
        except Exception as exc:
            return _err(f"{label} failed: {describe(exc)}")
        return render_result(result)

    return handler


def _schema(value: Any) -> dict[str, Any]:
    if isinstance(value, dict) and value.get("type") == "object":
        return value
    return dict(_EMPTY_SCHEMA)


async def build_mcp_toolset(config: AssistantConfig, assistant_id: uuid.UUID | None) -> McpToolset:
    """The tools this assistant version may use from its MCP servers."""
    toolset = McpToolset()
    if not config.mcp_servers or assistant_id is None:
        return toolset
    refs: dict[uuid.UUID, McpServerRef] = {}
    for ref in config.mcp_servers:
        try:
            refs[uuid.UUID(ref.id)] = ref
        except ValueError:
            continue
    async with get_sessionmaker()() as db:
        rows = (
            await db.scalars(
                select(McpServer).where(
                    McpServer.id.in_(refs),
                    McpServer.assistant_id == assistant_id,
                    McpServer.enabled.is_(True),
                )
            )
        ).all()
    for row in sorted(rows, key=lambda r: r.name):
        server = ServerSnapshot(
            id=row.id,
            name=row.name,
            transport=row.transport.value,
            command=row.command,
            args=list(row.args or []),
            url=row.url,
            sandbox=dict(row.sandbox or {}),
        )
        ref = refs[row.id]
        for spec in row.tools or []:
            name = str(spec.get("name", ""))
            if name not in ref.tools:
                continue
            description = str(spec.get("description") or "").strip()
            toolset.tools.append(
                CapabilityTool(
                    name=name,
                    description=(description or name) + f" (MCP server '{row.name}')",
                    input_schema=_schema(spec.get("input_schema")),
                    handler=_proxy(toolset, server, name),
                    read_only=spec.get("read_only") is True,
                    open_world=True,
                    server=row.name,
                    source_id=str(row.id),
                )
            )
            toolset.modes[toolset.tools[-1].qualified_name] = mcp_mode(
                config.approval_policy, ref, name
            )
    return toolset


__all__ = ["McpToolset", "ServerSnapshot", "build_mcp_toolset", "render_result"]
