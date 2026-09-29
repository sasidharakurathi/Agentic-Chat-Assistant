"""Graph references checked against what actually exists (tasks 1.1 / 3.11).

`graph/validate.py` is pure on purpose — no database, so it runs anywhere,
including in the browser's dry runs. That also means it never looked at what a
node *points at*. This module is the other half: it resolves every
`connection_id` and `data_source_id` against this assistant's own rows and
reports what does not hold up, as ordinary graph errors on the offending node.
MCP server nodes (Phase 4) are checked the same way.

Scoped to the assistant, not merely the org: a connection another assistant
owns is not this one's to wire in, even inside the same organization.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.datasources.sql_guard import Permissions
from app.graph.nodes import DatabaseNode, DataSourceNode, Graph, McpServerNode
from app.graph.validate import GraphIssue
from app.models.integration import DbConnection, McpServer
from app.models.rag import DataSource


def _as_uuid(value: object) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


async def _connections(
    session: AsyncSession, assistant_id: uuid.UUID, wanted: set[uuid.UUID]
) -> dict[uuid.UUID, DbConnection]:
    if not wanted:
        return {}
    rows = await session.scalars(
        select(DbConnection).where(
            DbConnection.id.in_(wanted), DbConnection.assistant_id == assistant_id
        )
    )
    return {c.id: c for c in rows.all()}


async def _source_ids(
    session: AsyncSession, assistant_id: uuid.UUID, wanted: set[uuid.UUID]
) -> set[uuid.UUID]:
    if not wanted:
        return set()
    rows = await session.scalars(
        select(DataSource.id).where(
            DataSource.id.in_(wanted), DataSource.assistant_id == assistant_id
        )
    )
    return set(rows.all())


async def _server_ids(
    session: AsyncSession, assistant_id: uuid.UUID, wanted: set[uuid.UUID]
) -> set[uuid.UUID]:
    if not wanted:
        return set()
    rows = await session.scalars(
        select(McpServer.id).where(McpServer.id.in_(wanted), McpServer.assistant_id == assistant_id)
    )
    return set(rows.all())


async def reference_issues(
    session: AsyncSession, assistant_id: uuid.UUID, graph: Graph
) -> list[GraphIssue]:
    db_nodes = [n for n in graph.nodes if isinstance(n, DatabaseNode)]
    ds_nodes = [n for n in graph.nodes if isinstance(n, DataSourceNode)]
    connections = await _connections(
        session, assistant_id, {u for n in db_nodes if (u := _as_uuid(n.data.connection_id))}
    )
    sources = await _source_ids(
        session, assistant_id, {u for n in ds_nodes if (u := _as_uuid(n.data.data_source_id))}
    )

    issues: list[GraphIssue] = []
    for db_node in db_nodes:
        cid = _as_uuid(db_node.data.connection_id)
        conn = connections.get(cid) if cid is not None else None
        if conn is None:
            # A malformed id used to publish cleanly and then raise ValueError
            # at the start of every turn.
            issues.append(
                GraphIssue(
                    code="unknown_connection",
                    message="This database connection does not exist on this assistant. "
                    "Pick one from the Databases tab, or remove the node.",
                    node_id=db_node.id,
                )
            )
        elif db_node.data.expose_write and not Permissions.from_dict(conn.permissions).write:
            issues.append(
                GraphIssue(
                    code="expose_write_without_write",
                    message=f"Writes are exposed, but '{conn.name}' is read-only. Allow writes "
                    "on the connection first, or turn off 'Expose writes'.",
                    node_id=db_node.id,
                )
            )

    for ds_node in ds_nodes:
        sid = _as_uuid(ds_node.data.data_source_id)
        if sid is None or sid not in sources:
            issues.append(
                GraphIssue(
                    code="unknown_data_source",
                    message="This data source does not exist on this assistant. "
                    "Pick one from the Sources tab, or remove the node.",
                    node_id=ds_node.id,
                )
            )

    mcp_nodes = [n for n in graph.nodes if isinstance(n, McpServerNode)]
    servers = await _server_ids(
        session, assistant_id, {u for n in mcp_nodes if (u := _as_uuid(n.data.mcp_server_id))}
    )
    for mcp_node in mcp_nodes:
        mid = _as_uuid(mcp_node.data.mcp_server_id)
        # A malformed id is already an error from the graph validator.
        if mid is not None and mid not in servers:
            issues.append(
                GraphIssue(
                    code="unknown_mcp_server",
                    message="This MCP server is not registered on this assistant. "
                    "Pick one from the MCP servers tab, or remove the node.",
                    node_id=mcp_node.id,
                )
            )
    return issues


__all__ = ["reference_issues"]
