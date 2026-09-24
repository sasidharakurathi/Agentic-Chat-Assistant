"""``mongo_find`` / ``mongo_aggregate`` (task 3.6).

Read-only by construction: there is no `mongo_update`, no `mongo_delete`, no
`mapReduce`. The plan calls v1 "reads only recommended"; this goes one step
further and makes writes simply unreachable through the agent, because the
alternative — a write tool guarded by policy — is a lot of surface area for a
capability nobody has asked for yet.

`app/datasources/mongodb.py` screens the pipeline for stages that would turn
a read into a write or into server-side JavaScript; this module is the
agent-facing wrapper around it.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from app.agent.caps import CapabilityTool, _err, _text
from app.datasources import DbError, get_mongo_adapter
from app.datasources.mongodb import (
    MongoBlocked,
    check_collection,
    check_pipeline,
    referenced_collections,
)
from app.datasources.sql_guard import Permissions
from app.db.session import get_sessionmaker
from app.logging import get_logger
from app.models.integration import DbConnection, DbEngine
from app.schemas.assistant_config import DatabaseRef
from app.services import db_connections as db_svc

log = get_logger(__name__)

_FIND_SCHEMA = {
    "type": "object",
    "properties": {
        "connection_id": {"type": "string"},
        "collection": {"type": "string"},
        "query": {"type": "object", "description": "A MongoDB filter document."},
        "projection": {"type": "object", "description": "Fields to return."},
        "limit": {"type": "integer"},
    },
    "required": ["connection_id", "collection"],
}

_AGGREGATE_SCHEMA = {
    "type": "object",
    "properties": {
        "connection_id": {"type": "string"},
        "collection": {"type": "string"},
        "pipeline": {"type": "array", "items": {"type": "object"}},
        "limit": {"type": "integer"},
    },
    "required": ["connection_id", "collection", "pipeline"],
}


def _as_dict(value: Any) -> dict[str, Any]:
    """Models sometimes hand back a JSON *string* where an object was asked
    for. Parsing it is friendlier than refusing on a formatting detail."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _as_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict)]
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, list):
            return [v for v in parsed if isinstance(v, dict)]
    return []


def _bounded_limit(raw: Any, row_limit: int) -> int:
    """A limit that is always a whole number in [1, row_limit].

    The old `min(int(limit), row_limit) if isinstance(limit, int)` let through
    every value that matters: 0 (pymongo: *no limit*), negatives (pymongo: one
    batch of |n|), and `True` (`isinstance(True, int)` is True). Zero or less
    is read as the model's likely intent — "all of it" — capped as usual.
    """
    if raw is None or isinstance(raw, bool):
        return row_limit
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return row_limit
    return row_limit if n <= 0 else min(n, row_limit)


def _authorise(
    perms: Permissions, database: str, collection: str, pipeline: list[dict[str, Any]] | None
) -> None:
    """The permission profile, applied the way `sql_guard` applies it to SQL.
    This module used to consult none of it."""
    if not perms.read:
        raise MongoBlocked("this connection cannot read")
    check_collection(collection, database, perms)
    if pipeline is not None:
        # Also screened again inside the adapter; checked here so the
        # refusal never depends on which adapter happens to be wired in.
        check_pipeline(pipeline)
        for joined in referenced_collections(pipeline):
            check_collection(joined, database, perms)


def _render(result: Any, what: str) -> str:
    lines = [f"{what}: {result.row_count} document(s) in {result.elapsed_ms}ms"]
    if result.columns:
        lines.append(json.dumps({"fields": result.columns, "rows": result.rows}, default=str))
    if result.truncated:
        lines.append("NOTE: results were truncated; this is not the complete answer.")
    return "\n".join(lines)


def build_mongo_tools(
    assistant_id: uuid.UUID, databases: list[DatabaseRef]
) -> list[CapabilityTool]:
    if not databases:
        return []
    refs = {ref.connection_id: ref for ref in databases}

    async def _load(connection_id: str) -> tuple[DbConnection, Any, Permissions]:
        if connection_id not in refs:
            raise MongoBlocked(f"{connection_id} is not a database this assistant can use")
        cid = uuid.UUID(connection_id)
        sessionmaker = get_sessionmaker()
        async with sessionmaker() as session:
            conn = await db_svc.get(session, assistant_id=assistant_id, connection_id=cid)
            info = await db_svc.connection_info(session, conn)
        if conn.engine != DbEngine.mongodb:
            raise MongoBlocked("this connection is not MongoDB; use sql_query")
        return conn, info, Permissions.from_dict(conn.permissions)

    async def find(args: dict[str, Any]) -> dict[str, Any]:
        try:
            conn, info, perms = await _load(str(args.get("connection_id", "")))
        except Exception as exc:
            return _err(f"error: {exc}")

        collection = str(args.get("collection", ""))
        capped = _bounded_limit(args.get("limit"), perms.row_limit)
        try:
            _authorise(perms, info.database, collection, None)
            result = await get_mongo_adapter().find(
                info,
                collection=collection,
                query=_as_dict(args.get("query")),
                projection=_as_dict(args.get("projection")) or None,
                limit=capped,
                timeout_ms=perms.statement_timeout_ms,
            )
        except MongoBlocked as exc:
            log.info("mongo_blocked", connection=str(conn.id), reason=str(exc))
            return _err(f"blocked: {exc}")
        except DbError as exc:
            return _err(f"error: {exc}")
        return _text(_render(result, "find"))

    async def aggregate(args: dict[str, Any]) -> dict[str, Any]:
        try:
            conn, info, perms = await _load(str(args.get("connection_id", "")))
        except Exception as exc:
            return _err(f"error: {exc}")

        pipeline = _as_list(args.get("pipeline"))
        if not pipeline:
            return _err("error: pipeline must be a non-empty array of stages")
        collection = str(args.get("collection", ""))
        capped = _bounded_limit(args.get("limit"), perms.row_limit)
        try:
            _authorise(perms, info.database, collection, pipeline)
            result = await get_mongo_adapter().aggregate(
                info,
                collection=collection,
                pipeline=pipeline,
                limit=capped,
                timeout_ms=perms.statement_timeout_ms,
            )
        except MongoBlocked as exc:
            log.info("mongo_blocked", connection=str(conn.id), reason=str(exc))
            return _err(f"blocked: {exc}")
        except DbError as exc:
            return _err(f"error: {exc}")
        return _text(_render(result, "aggregate"))

    return [
        CapabilityTool(
            name="mongo_find",
            description=(
                "Read documents from a MongoDB collection with a filter. "
                "Use sql_list_schemas for connection ids and sql_introspect for "
                "the fields a collection tends to have."
            ),
            input_schema=_FIND_SCHEMA,
            handler=find,
        ),
        CapabilityTool(
            name="mongo_aggregate",
            description=(
                "Run a read-only MongoDB aggregation pipeline. Stages that write "
                "($out, $merge) or run JavaScript ($where, $function) are rejected."
            ),
            input_schema=_AGGREGATE_SCHEMA,
            handler=aggregate,
        ),
    ]


__all__ = ["build_mongo_tools"]
