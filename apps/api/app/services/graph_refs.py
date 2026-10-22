"""Graph references checked against what actually exists (tasks 1.1 / 3.11).

`graph/validate.py` is pure on purpose — no database, so it runs anywhere,
including in the browser's dry runs. That also means it never looked at what a
node *points at*. This module is the other half: it resolves every
`connection_id` and `data_source_id` against this assistant's own rows and
reports what does not hold up, as ordinary graph errors on the offending node.
MCP server nodes (Phase 4) are checked the same way, and so are the HTTP
connections the HTTP request tool's actions call (Phase 9.3), and the action
a hand-off opens tickets with (Phase 9.5).

Scoped to the assistant, not merely the org: a connection another assistant
owns is not this one's to wire in, even inside the same organization.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.datasources.sql_guard import Permissions
from app.graph import fixes
from app.graph.nodes import (
    AgentNode,
    DatabaseNode,
    DataSourceNode,
    Graph,
    McpServerNode,
    ToolNode,
)
from app.graph.validate import GraphIssue
from app.models.integration import DbConnection, HttpConnection, McpServer
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


async def _http_connections(
    session: AsyncSession, assistant_id: uuid.UUID, wanted: set[uuid.UUID]
) -> dict[uuid.UUID, HttpConnection]:
    if not wanted:
        return {}
    rows = await session.scalars(
        select(HttpConnection).where(
            HttpConnection.id.in_(wanted), HttpConnection.assistant_id == assistant_id
        )
    )
    return {c.id: c for c in rows.all()}


async def _http_action_issues(
    session: AsyncSession, assistant_id: uuid.UUID, graph: Graph
) -> list[GraphIssue]:
    """Each HTTP action's connection exists on this assistant, and a
    `confirm` action's connection tells the system who is asking: the
    person confirms as themselves, so the system has to know who that is."""
    issues: list[GraphIssue] = []
    nodes = [n for n in graph.nodes if isinstance(n, ToolNode) and n.data.key == "http_request"]
    declared: list[tuple[ToolNode, dict[str, object]]] = []
    for node in nodes:
        raw_actions = node.data.config.get("actions")
        if isinstance(raw_actions, list):
            declared.extend((node, raw) for raw in raw_actions if isinstance(raw, dict))
    conns = await _http_connections(
        session,
        assistant_id,
        {u for _, raw in declared if (u := _as_uuid(raw.get("connection_id")))},
    )
    for node, raw in declared:
        title = str(raw.get("title") or raw.get("name") or "an action")
        cid = _as_uuid(raw.get("connection_id"))
        conn = conns.get(cid) if cid else None
        if conn is None:
            issues.append(
                GraphIssue(
                    code="unknown_http_connection",
                    message=f"'{title}' calls an HTTP connection this assistant doesn't have. "
                    "Pick one in the HTTP request tool's settings.",
                    node_id=node.id,
                )
            )
        elif raw.get("tier") == "confirm" and not conn.send_asker:
            issues.append(
                GraphIssue(
                    code="confirm_without_asker",
                    message=f"'{title}' is confirmed by the person asking, but '{conn.name}' "
                    "doesn't tell the system who they are. Turn on 'Say who is asking' "
                    "on the connection, or make the action wait for an approver.",
                    node_id=node.id,
                )
            )
    return issues


#: The values a hand-off fills in an action's parameters (Phase 9.5).
HANDOFF_PARAMS = frozenset({"title", "summary", "note"})


def _items(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _handoff_issues(graph: Graph) -> list[GraphIssue]:
    """The action a hand-off opens tickets with (Phase 9.5) is one of the
    HTTP request tool's, runs without an approver (the person asking for
    help is the one who confirms it) and needs nothing a hand-off can't
    fill in."""
    agent = next((n for n in graph.nodes if isinstance(n, AgentNode)), None)
    if agent is None or not agent.data.handoff.enabled or not agent.data.handoff.action:
        return []
    name = agent.data.handoff.action
    declared = [
        raw
        for n in graph.nodes
        if isinstance(n, ToolNode) and n.data.key == "http_request"
        for raw in _items(n.data.config.get("actions"))
        if isinstance(raw, dict)
    ]
    raw = next((r for r in declared if r.get("name") == name), None)
    if raw is None:
        message = (
            f"The hand-off opens tickets with '{name}', but the HTTP request tool has no "
            "action by that name. Pick another in Settings (Hand-off), or clear it."
        )
        return [GraphIssue(code="handoff_unknown_action", message=message, node_id=agent.id)]
    title = str(raw.get("title") or name)
    if raw.get("tier", "approver") == "approver":
        message = (
            f"The hand-off can't open tickets with '{title}': it waits for an approver, and "
            "someone asking for help can't wait. Make it 'confirm' (their click confirms "
            "it), or pick another action."
        )
        return [GraphIssue(code="handoff_approver_action", message=message, node_id=agent.id)]
    unfilled = [
        str(p.get("name"))
        for p in _items(raw.get("params"))
        if isinstance(p, dict) and p.get("required", True) and p.get("name") not in HANDOFF_PARAMS
    ]
    if unfilled:
        message = (
            f"'{title}' needs {unfilled[0]}, which a hand-off can't fill in: it sends only a "
            "title, a summary and a note. Make it optional, or pick another action."
        )
        return [GraphIssue(code="handoff_action_params", message=message, node_id=agent.id)]
    return []


async def _handoff_contact_issues(
    session: AsyncSession, assistant_id: uuid.UUID, graph: Graph
) -> list[GraphIssue]:
    """A hand-off by email (Phase 9.5) links to the conversation, which only
    an admin may open (with a recorded reason): a contact who isn't one
    would get a link that refuses them (Phase 9.9)."""
    from sqlalchemy import func

    from app.models.assistant import Assistant
    from app.models.enums import MemberRole
    from app.models.membership import Membership
    from app.models.user import User

    agent = next((n for n in graph.nodes if isinstance(n, AgentNode)), None)
    if agent is None or not agent.data.handoff.enabled or agent.data.handoff.action:
        return []
    email = agent.data.handoff.contact_email.strip().lower()
    assistant = await session.get(Assistant, assistant_id)
    if not email or assistant is None:
        return []
    role = await session.scalar(
        select(Membership.role)
        .join(User, User.id == Membership.user_id)
        .where(
            Membership.org_id == assistant.org_id,
            func.lower(User.email) == email,
            Membership.deactivated_at.is_(None),
            User.is_active.is_(True),
        )
    )
    if role is not None and role.satisfies(MemberRole.admin):
        return []
    message = (
        f"The hand-off emails {email}, who isn't an admin here, so they couldn't open the "
        "conversation it links to. Use an admin's address, or open tickets with an action."
    )
    return [GraphIssue(code="handoff_contact_not_admin", message=message, node_id=agent.id)]


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
                    fix=fixes.remove_node(db_node.id),
                )
            )
        elif db_node.data.expose_write and not Permissions.from_dict(conn.permissions).write:
            issues.append(
                GraphIssue(
                    code="expose_write_without_write",
                    message=f"Writes are exposed, but '{conn.name}' is read-only. Allow writes "
                    "on the connection first, or turn off 'Expose writes'.",
                    node_id=db_node.id,
                    fix=fixes.patch(db_node.id, {"expose_write": False}, "Turn off Expose writes"),
                )
            )
        if conn is not None and not Permissions.from_dict(conn.permissions).write:
            # A declared action that changes data (Phase 9.1) could never run.
            writers = [a.title for a in db_node.data.actions if a.tier != "read"]
            if writers:
                issues.append(
                    GraphIssue(
                        code="action_without_write",
                        message=f"'{writers[0]}' changes data, but '{conn.name}' is read-only. "
                        "Allow writes on the connection, or remove the action.",
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
                    fix=fixes.remove_node(ds_node.id),
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
                    fix=fixes.remove_node(mcp_node.id),
                )
            )
    issues.extend(await _http_action_issues(session, assistant_id, graph))
    issues.extend(_handoff_issues(graph))
    issues.extend(await _handoff_contact_issues(session, assistant_id, graph))
    return issues


__all__ = ["reference_issues"]
