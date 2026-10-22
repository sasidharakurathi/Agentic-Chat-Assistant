"""HTTP connections (Phase 9.3): a company system an assistant calls.

The header values (an API key, a bearer token) are sealed in `secrets` as
one JSON object, like an MCP server's, and opened only to send a request.
The base URL is checked here for shape; whether it may be reached (a
public address, or one of the organisation's internal destinations) is
checked on every request (`security/ssrf.py`).
"""

from __future__ import annotations

import uuid
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import BadRequest, Conflict, NotFound
from app.models.assistant import Assistant
from app.models.integration import HttpConnection
from app.models.secret import SecretKind
from app.schemas.http_connection import HttpConnectionCreate, HttpConnectionUpdate
from app.services import audit
from app.services.mcp_servers import drop_secret, open_map, seal_map


def check_base_url(url: str) -> str:
    """https://host[:port][/path], no credentials, query or fragment. Plain
    http only for an internal destination that allows it, checked when a
    request is sent."""
    parts = urlsplit(url.strip())
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise BadRequest(
            "The base URL is like https://glpi.corp.example.com/api", code="invalid_url"
        )
    if parts.username or parts.password:
        raise BadRequest("Put credentials in a header, not the URL", code="invalid_url")
    if parts.query or parts.fragment:
        raise BadRequest("The base URL has no query or fragment", code="invalid_url")
    return url.strip().rstrip("/")


async def open_headers(session: AsyncSession, conn: HttpConnection) -> dict[str, str]:
    return await open_map(session, conn.headers_secret_ref)


async def get(
    session: AsyncSession, *, assistant_id: uuid.UUID, connection_id: uuid.UUID
) -> HttpConnection:
    conn = await session.scalar(
        select(HttpConnection).where(
            HttpConnection.id == connection_id, HttpConnection.assistant_id == assistant_id
        )
    )
    if conn is None:
        raise NotFound("HTTP connection not found")
    return conn


async def list_for_assistant(
    session: AsyncSession, assistant_id: uuid.UUID
) -> list[HttpConnection]:
    return list(
        (
            await session.scalars(
                select(HttpConnection)
                .where(HttpConnection.assistant_id == assistant_id)
                .order_by(HttpConnection.name)
            )
        ).all()
    )


async def _name_taken(
    session: AsyncSession, assistant_id: uuid.UUID, name: str, but: uuid.UUID | None = None
) -> bool:
    query = select(HttpConnection.id).where(
        HttpConnection.assistant_id == assistant_id, HttpConnection.name == name
    )
    if but is not None:
        query = query.where(HttpConnection.id != but)
    return await session.scalar(query) is not None


def _facts(conn: HttpConnection) -> dict[str, Any]:
    return {
        "name": conn.name,
        "base_url": conn.base_url,
        "header_names": sorted(conn.header_names or []),
        "send_asker": conn.send_asker,
    }


async def create(
    session: AsyncSession,
    *,
    assistant: Assistant,
    body: HttpConnectionCreate,
    user_id: uuid.UUID,
    ip: str | None = None,
) -> HttpConnection:
    if await _name_taken(session, assistant.id, body.name):
        raise Conflict(f"There is already an HTTP connection called {body.name}", code="name_taken")
    conn = HttpConnection(
        assistant_id=assistant.id,
        org_id=assistant.org_id,
        name=body.name,
        base_url=check_base_url(body.base_url),
        headers_secret_ref=await seal_map(
            session, org_id=assistant.org_id, values=body.headers, kind=SecretKind.http_headers
        ),
        header_names=sorted(body.headers),
        send_asker=body.send_asker,
    )
    session.add(conn)
    await session.flush()
    await audit.record(
        session,
        action="http_connection.create",
        org_id=assistant.org_id,
        actor_user_id=user_id,
        target_type="http_connection",
        target_id=conn.id,
        meta=_facts(conn),
        ip=ip,
    )
    await session.commit()
    await session.refresh(conn)
    return conn


async def update(
    session: AsyncSession,
    conn: HttpConnection,
    body: HttpConnectionUpdate,
    *,
    user_id: uuid.UUID,
    ip: str | None = None,
) -> HttpConnection:
    before = _facts(conn)
    if body.name is not None and body.name != conn.name:
        if await _name_taken(session, conn.assistant_id, body.name, conn.id):
            raise Conflict(
                f"There is already an HTTP connection called {body.name}", code="name_taken"
            )
        conn.name = body.name
    if body.base_url is not None:
        moved = check_base_url(body.base_url)
        if moved != conn.base_url and body.headers is None and conn.header_names:
            # Its sealed credential was for the old address (Phase 9.9): a
            # co-editor could otherwise point it at their own server.
            raise BadRequest(
                "Enter the headers again for the new address: the stored ones were for the old one",
                code="headers_required",
            )
        conn.base_url = moved
    if body.send_asker is not None:
        conn.send_asker = body.send_asker
    if body.headers is not None:
        old = conn.headers_secret_ref
        conn.headers_secret_ref = await seal_map(
            session, org_id=conn.org_id, values=body.headers, kind=SecretKind.http_headers
        )
        conn.header_names = sorted(body.headers)
        await drop_secret(session, old)
    await audit.record(
        session,
        action="http_connection.update",
        org_id=conn.org_id,
        actor_user_id=user_id,
        target_type="http_connection",
        target_id=conn.id,
        meta={"from": before, "to": _facts(conn), "headers_replaced": body.headers is not None},
        ip=ip,
    )
    await session.commit()
    await session.refresh(conn)
    return conn


async def delete(
    session: AsyncSession, conn: HttpConnection, *, user_id: uuid.UUID, ip: str | None = None
) -> None:
    await audit.record(
        session,
        action="http_connection.delete",
        org_id=conn.org_id,
        actor_user_id=user_id,
        target_type="http_connection",
        target_id=conn.id,
        meta=_facts(conn),
        ip=ip,
    )
    await drop_secret(session, conn.headers_secret_ref)
    await session.delete(conn)
    await session.commit()


__all__ = [
    "check_base_url",
    "create",
    "delete",
    "get",
    "list_for_assistant",
    "open_headers",
    "update",
]
