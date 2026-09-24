"""``sql_list_schemas`` / ``sql_introspect`` / ``sql_query`` (task 3.5).

Built by factories like ``caps_rag``'s knowledge-base tools, because they
close over *which* connections this assistant may use. Three tools rather
than one on purpose — they map onto how an agent actually works a database:
find out what exists, look at the shape of the relevant tables, then ask one
question. Collapsing them into a single "run SQL" tool would mean the model
guessing table names, and guessing is how you get five failed queries per
answer.

**`expose_write` is a second gate, not a duplicate of one.** The connection's
own `permissions.write` says what the *credential* may do; the assistant's
`DatabaseRef.expose_write` says what *this assistant* may ask for. Both must
agree, so pointing a second assistant at a writable connection does not
silently hand it write access.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from typing import Any

from app.agent.caps import CapabilityTool, _err, _text
from app.api.errors import AppError
from app.datasources import ConnectionInfo, DbError, get_sql_adapter
from app.datasources.sql_guard import Permissions, SqlBlocked, guard
from app.db.session import get_sessionmaker
from app.logging import get_logger
from app.models.integration import DbConnection, DbEngine
from app.schemas.assistant_config import DatabaseRef
from app.schemas.db_connection import DbSchemaOut
from app.services import db_connections as db_svc

log = get_logger(__name__)

_QUERY_SCHEMA = {
    "type": "object",
    "properties": {
        "connection_id": {
            "type": "string",
            "description": "Which database, from sql_list_schemas.",
        },
        "sql": {"type": "string", "description": "One SQL statement."},
    },
    "required": ["connection_id", "sql"],
}

_INTROSPECT_SCHEMA = {
    "type": "object",
    "properties": {
        "connection_id": {"type": "string"},
        "table": {
            "type": "string",
            "description": "Optional — narrow to one table instead of the whole schema.",
        },
    },
    "required": ["connection_id"],
}


async def _load(
    assistant_id: uuid.UUID, refs: dict[str, DatabaseRef], connection_id: str
) -> tuple[DbConnection, DatabaseRef, Any]:
    """Resolve a connection the *agent* named, re-checking it belongs to this
    assistant. The id arrives from model output, so it is untrusted input:
    scoping the lookup to `assistant_id` is what stops a hallucinated (or
    injected) uuid reaching another tenant's database."""
    ref = refs.get(connection_id)
    if ref is None:
        raise SqlBlocked(f"{connection_id} is not a database this assistant can use")
    try:
        cid = uuid.UUID(connection_id)
    except ValueError as exc:
        raise SqlBlocked("connection_id is not a valid id") from exc

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        conn = await db_svc.get(session, assistant_id=assistant_id, connection_id=cid)
        info = await db_svc.connection_info(session, conn)
    return conn, ref, info


def _writable(info: ConnectionInfo) -> ConnectionInfo:
    """SQLite is the one engine whose read-only-ness comes from *how the file
    is opened* rather than from the credential, so a write the guard has
    already approved needs the handle upgraded explicitly."""
    return replace(info, options={**info.options, "writable": True})


def _effective_permissions(conn: DbConnection, ref: DatabaseRef) -> Permissions:
    perms = Permissions.from_dict(conn.permissions)
    if not ref.expose_write:
        # The assistant wasn't granted writes even if the credential has them.
        perms.write = False
        perms.ddl = False
    return perms


def _render(result: Any, guarded: Any) -> str:
    lines = [f"sql: {guarded.statement}"]
    if guarded.limit_applied is not None:
        lines.append(f"(row limit {guarded.limit_applied} applied)")
    if guarded.mutating:
        # `affected_rows`, never `row_count`: a write returns no rows, so
        # row_count is 0 however many it changed. Telling someone who just
        # approved a DELETE that it affected 0 rows invites them to run it
        # again. None means the driver genuinely could not say (DDL).
        if result.affected_rows is None:
            lines.append("statement completed")
        else:
            lines.append(f"{result.affected_rows} row(s) affected")
    else:
        lines.append(f"{result.row_count} row(s) in {result.elapsed_ms}ms")
        if result.columns:
            lines.append(json.dumps({"columns": result.columns, "rows": result.rows}, default=str))
    if result.truncated:
        lines.append("NOTE: results were truncated; this is not the complete answer.")
    return "\n".join(lines)


async def _current_schema(assistant_id: uuid.UUID, connection_id: uuid.UUID) -> DbSchemaOut:
    """The permission-filtered schema, refreshed when missing or past its TTL."""
    async with get_sessionmaker()() as session:
        fresh = await db_svc.get(session, assistant_id=assistant_id, connection_id=connection_id)
        cached = await db_svc.cached_schema(session, fresh)
        if cached.schemas and not db_svc.schema_is_stale(cached):
            return cached
        # First use, a cache dropped by a permission change, or one past its
        # TTL. Refresh now rather than telling the agent "no schema" and
        # watching it guess. If a *stale* cache cannot be refreshed, it is
        # still better than nothing.
        try:
            return await db_svc.refresh_schema(session, fresh)
        except AppError:
            if not cached.schemas:
                raise
            return cached


def build_sql_tools(assistant_id: uuid.UUID, databases: list[DatabaseRef]) -> list[CapabilityTool]:
    if not databases:
        return []
    refs = {ref.connection_id: ref for ref in databases}
    any_write = any(ref.expose_write for ref in databases)

    async def list_schemas(_args: dict[str, Any]) -> dict[str, Any]:
        sessionmaker = get_sessionmaker()
        async with sessionmaker() as session:
            conns = await db_svc.list_for_assistant(session, assistant_id)
        usable = [c for c in conns if str(c.id) in refs]
        if not usable:
            return _text("No databases are wired into this assistant.")
        lines = []
        for c in usable:
            perms = _effective_permissions(c, refs[str(c.id)])
            mode = "read/write" if perms.write else "read-only"
            lines.append(
                f"- {c.name} (connection_id={c.id}, engine={c.engine.value}, "
                f"database={c.database}, {mode}, status={c.status.value})"
            )
        return _text("\n".join(lines))

    async def introspect(args: dict[str, Any]) -> dict[str, Any]:
        try:
            conn, _ref, _info = await _load(assistant_id, refs, str(args.get("connection_id", "")))
        except Exception as exc:
            return _err(f"error: {exc}")

        cached = await _current_schema(assistant_id, conn.id)

        wanted = str(args.get("table", "")).strip().lower()
        namespaces = cached.schemas
        if wanted:
            namespaces = [
                ns.model_copy(update={"tables": [t for t in ns.tables if t.name.lower() == wanted]})
                for ns in namespaces
            ]
            namespaces = [ns for ns in namespaces if ns.tables]
            if not namespaces:
                return _text(f"No table named {wanted!r} is visible on this connection.")
        payload = [ns.model_dump() for ns in namespaces]
        return _text(json.dumps({"schemas": payload}, default=str))

    async def run_query(args: dict[str, Any]) -> dict[str, Any]:
        sql = str(args.get("sql", "")).strip()
        if not sql:
            return _err("error: no sql provided")
        try:
            conn, ref, info = await _load(assistant_id, refs, str(args.get("connection_id", "")))
        except Exception as exc:
            return _err(f"error: {exc}")

        if conn.engine == DbEngine.mongodb:
            return _err("error: this is a MongoDB connection; use mongo_find / mongo_aggregate")

        perms = _effective_permissions(conn, ref)
        try:
            guarded = guard(sql, engine=conn.engine.value, permissions=perms)
        except SqlBlocked as exc:
            # The model is told *why*, so it can try a legal alternative
            # instead of retrying the same rejected statement.
            log.info("sql_blocked", connection=str(conn.id), reason=str(exc))
            return _err(f"blocked: {exc}")

        # SQLite writes need a writable handle; the guard has already decided
        # whether a write is allowed at all.
        if guarded.mutating and conn.engine == DbEngine.sqlite:
            info = _writable(info)

        adapter = get_sql_adapter(conn.engine.value)
        try:
            result = await adapter.run(
                info,
                guarded.statement,
                timeout_ms=perms.statement_timeout_ms,
                max_rows=perms.row_limit,
                mutating=guarded.mutating,
                returns_rows=guarded.returns_rows,
            )
        except DbError as exc:
            return _err(f"error: {exc}")
        return _text(_render(result, guarded))

    tools = [
        CapabilityTool(
            name="sql_list_schemas",
            description=(
                "List the databases this assistant can query, with their engine and "
                "whether they are read-only. Start here to get a connection_id."
            ),
            input_schema={"type": "object", "properties": {}},
            handler=list_schemas,
        ),
        CapabilityTool(
            name="sql_introspect",
            description=(
                "Show the tables and columns of a database (optionally one table). "
                "Use this before writing SQL rather than guessing table names."
            ),
            input_schema=_INTROSPECT_SCHEMA,
            handler=introspect,
        ),
        CapabilityTool(
            name="sql_query",
            description=(
                "Run one SQL statement against a database. Read queries are capped and "
                "get a LIMIT applied automatically."
                + (
                    " Statements that modify data require human approval."
                    if any_write
                    else " This assistant's databases are read-only."
                )
            ),
            input_schema=_QUERY_SCHEMA,
            handler=run_query,
            read_only=not any_write,
        ),
    ]
    return tools


__all__ = ["build_sql_tools"]
