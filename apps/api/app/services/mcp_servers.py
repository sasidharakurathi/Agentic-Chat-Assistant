"""MCP server registrations (task 4.3).

Mirrors `services/db_connections.py`. Header and environment values are
sealed in `secrets` as one JSON object each, and opened only by
`open_headers` / `open_env`, which the runtime calls at the moment it
connects (tasks 4.5 / 4.6). Nothing that returns to a client can carry them.
"""

from __future__ import annotations

import json
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import BadRequest, Conflict, NotFound
from app.db.pagination import PageResult, keyset_page
from app.logging import get_logger
from app.mcp.limits import SandboxLimits
from app.models.assistant import Assistant
from app.models.integration import McpServer, McpServerStatus, McpTransport
from app.models.secret import Secret, SecretKind
from app.schemas.mcp_server import McpServerCreate, McpServerSummary, McpServerUpdate
from app.security.crypto import CryptoError, SealedSecret, open_sealed, seal
from app.services import audit

log = get_logger(__name__)


def to_summary(server: McpServer) -> McpServerSummary:
    summary = McpServerSummary.model_validate(
        {
            **{c: getattr(server, c) for c in McpServerSummary.model_fields if c != "sandbox"},
            "sandbox": SandboxLimits.from_row(server.sandbox),
        }
    )
    return summary


# ── sealed maps ──────────────────────────────────────────────


async def _seal_map(
    session: AsyncSession, *, org_id: uuid.UUID, values: dict[str, str], kind: SecretKind
) -> uuid.UUID | None:
    """One sealed row for the whole map; none at all for an empty one."""
    if not values:
        return None
    sealed = seal(json.dumps(values, sort_keys=True), kind=kind.value)
    row = Secret(
        org_id=org_id,
        kind=kind,
        ciphertext=sealed.ciphertext,
        dek_wrapped=sealed.dek_wrapped,
        nonce=sealed.nonce,
    )
    session.add(row)
    await session.flush()
    return row.id


async def _drop_secret(session: AsyncSession, secret_ref: uuid.UUID | None) -> None:
    if secret_ref is None:
        return
    row = await session.get(Secret, secret_ref)
    if row is not None:
        await session.delete(row)


async def _open_map(session: AsyncSession, secret_ref: uuid.UUID | None) -> dict[str, str]:
    if secret_ref is None:
        return {}
    row = await session.get(Secret, secret_ref)
    if row is None:
        return {}
    try:
        plain = open_sealed(
            SealedSecret(ciphertext=row.ciphertext, dek_wrapped=row.dek_wrapped, nonce=row.nonce),
            kind=row.kind.value,
        )
    except CryptoError:
        log.error("secret_decrypt_failed", secret_id=str(secret_ref))
        raise BadRequest(
            "stored credential could not be decrypted (APP_KEK may have changed)",
            code="secret_undecryptable",
        ) from None
    value = json.loads(plain)
    return {str(k): str(v) for k, v in value.items()}


async def open_headers(session: AsyncSession, server: McpServer) -> dict[str, str]:
    """The decrypted headers, for the connection being opened right now."""
    return await _open_map(session, server.headers_secret_ref)


async def open_env(session: AsyncSession, server: McpServer) -> dict[str, str]:
    """The decrypted environment, for the process being started right now."""
    return await _open_map(session, server.env_secret_ref)


# ── CRUD ─────────────────────────────────────────────────────


async def _name_taken(
    session: AsyncSession, assistant_id: uuid.UUID, name: str, *, except_id: uuid.UUID | None
) -> bool:
    q = select(McpServer.id).where(McpServer.assistant_id == assistant_id, McpServer.name == name)
    if except_id is not None:
        q = q.where(McpServer.id != except_id)
    return (await session.scalar(q)) is not None


def _conflict(name: str) -> Conflict:
    return Conflict(
        f"This assistant already has an MCP server named '{name}'. Names must be unique: "
        "they prefix the server's tool names.",
        code="mcp_server_name_taken",
    )


async def create(
    session: AsyncSession,
    *,
    assistant: Assistant,
    body: McpServerCreate,
    user_id: uuid.UUID,
    ip: str | None = None,
) -> McpServer:
    if await _name_taken(session, assistant.id, body.name, except_id=None):
        raise _conflict(body.name)
    server = McpServer(
        assistant_id=assistant.id,
        org_id=assistant.org_id,
        name=body.name,
        transport=McpTransport(body.transport),
        command=body.command,
        args=list(body.args),
        url=body.url,
        headers_secret_ref=await _seal_map(
            session, org_id=assistant.org_id, values=body.headers, kind=SecretKind.mcp_headers
        ),
        header_names=sorted(body.headers),
        env_secret_ref=await _seal_map(
            session, org_id=assistant.org_id, values=body.env, kind=SecretKind.mcp_env
        ),
        env_keys=sorted(body.env),
        enabled=body.enabled,
        tools=[],
        sandbox=body.sandbox.model_dump() if body.sandbox else {},
        status=McpServerStatus.unknown,
    )
    session.add(server)
    try:
        await session.flush()
    except IntegrityError:
        # Lost a race with a concurrent create of the same name.
        await session.rollback()
        raise _conflict(body.name) from None
    await audit.record(
        session,
        action="mcp_server.create",
        org_id=assistant.org_id,
        actor_user_id=user_id,
        target_type="mcp_server",
        target_id=server.id,
        # What it is and where it points; header/env *names* only.
        meta={
            "transport": body.transport,
            "command": body.command,
            "url": body.url,
            "header_names": server.header_names,
            "env_keys": server.env_keys,
        },
        ip=ip,
    )
    await session.commit()
    return server


async def list_for_assistant(session: AsyncSession, assistant_id: uuid.UUID) -> list[McpServer]:
    rows = await session.scalars(
        select(McpServer)
        .where(McpServer.assistant_id == assistant_id)
        .order_by(McpServer.created_at.desc(), McpServer.id.desc())
    )
    return list(rows)


async def page_for_assistant(
    session: AsyncSession, assistant_id: uuid.UUID, *, limit: int = 50, cursor: str | None = None
) -> PageResult[McpServer]:
    return await keyset_page(
        session,
        select(McpServer).where(McpServer.assistant_id == assistant_id),
        [McpServer.created_at, McpServer.id],
        limit=limit,
        cursor=cursor,
    )


async def get(session: AsyncSession, *, assistant_id: uuid.UUID, server_id: uuid.UUID) -> McpServer:
    server = await session.scalar(
        select(McpServer).where(McpServer.id == server_id, McpServer.assistant_id == assistant_id)
    )
    if server is None:
        raise NotFound("MCP server not found")
    return server


def _check_shape(server: McpServer, data: dict[str, object]) -> None:
    """An update may not give a server fields of the other transport."""
    stdio = server.transport == McpTransport.stdio
    if not stdio and "sandbox" in data:
        raise BadRequest(
            "resource limits apply to local-command (stdio) servers only",
            code="mcp_wrong_fields",
        )
    if stdio and ({"url", "headers"} & set(data)):
        raise BadRequest(
            "a stdio server takes a command and environment, not a URL or headers",
            code="mcp_wrong_fields",
        )
    if not stdio and ({"command", "args", "env"} & set(data)):
        raise BadRequest(
            f"an {server.transport.value} server takes a URL and headers, not a command",
            code="mcp_wrong_fields",
        )
    if stdio and "command" in data and not str(data["command"] or "").strip():
        raise BadRequest("a stdio server needs a command", code="mcp_wrong_fields")


async def update(
    session: AsyncSession,
    server: McpServer,
    body: McpServerUpdate,
    *,
    user_id: uuid.UUID,
    ip: str | None = None,
) -> McpServer:
    data = body.model_dump(exclude_unset=True, exclude_none=True)
    _check_shape(server, data)
    headers = data.pop("headers", None)
    env = data.pop("env", None)

    if "sandbox" in data:
        # Only what the request set, so an update of one limit keeps the others.
        data["sandbox"] = body.sandbox.model_dump(exclude_unset=True) if body.sandbox else {}
    renamed = "name" in data and data["name"] != server.name
    if renamed and await _name_taken(
        session, server.assistant_id, data["name"], except_id=server.id
    ):
        raise _conflict(data["name"])
    if "command" in data:
        data["command"] = str(data["command"]).strip()

    # A different program or endpoint may offer different tools: what was
    # discovered no longer describes it, so it is cleared rather than left
    # to be trusted.
    moved = any(k in data and data[k] != getattr(server, k) for k in ("command", "args", "url"))
    sandbox = data.pop("sandbox", None)
    for field, value in data.items():
        setattr(server, field, value)
    if sandbox is not None:
        # Only the limits sent change; the rest keep their current values.
        server.sandbox = {**(server.sandbox or {}), **sandbox}
    if headers is not None:
        old = server.headers_secret_ref
        server.headers_secret_ref = await _seal_map(
            session, org_id=server.org_id, values=headers, kind=SecretKind.mcp_headers
        )
        server.header_names = sorted(headers)
        await _drop_secret(session, old)
    if env is not None:
        old = server.env_secret_ref
        server.env_secret_ref = await _seal_map(
            session, org_id=server.org_id, values=env, kind=SecretKind.mcp_env
        )
        server.env_keys = sorted(env)
        await _drop_secret(session, old)

    if moved:
        server.tools = []
        server.tools_discovered_at = None
    if moved or headers is not None or env is not None:
        server.status = McpServerStatus.unknown
        server.error = None

    await audit.record(
        session,
        action="mcp_server.update",
        org_id=server.org_id,
        actor_user_id=user_id,
        target_type="mcp_server",
        target_id=server.id,
        meta={
            "fields": sorted(data)
            + (["headers"] if headers is not None else [])
            + (["env"] if env is not None else [])
            + (["sandbox"] if sandbox is not None else [])
        },
        ip=ip,
    )
    await session.commit()
    return server


async def delete(
    session: AsyncSession, server: McpServer, *, user_id: uuid.UUID, ip: str | None = None
) -> None:
    await audit.record(
        session,
        action="mcp_server.delete",
        org_id=server.org_id,
        actor_user_id=user_id,
        target_type="mcp_server",
        target_id=server.id,
        meta={"name": server.name},
        ip=ip,
    )
    await _drop_secret(session, server.headers_secret_ref)
    await _drop_secret(session, server.env_secret_ref)
    await session.delete(server)
    await session.commit()


__all__ = [
    "create",
    "delete",
    "get",
    "list_for_assistant",
    "open_env",
    "open_headers",
    "page_for_assistant",
    "to_summary",
    "update",
]
