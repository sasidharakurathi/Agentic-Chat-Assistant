"""MongoDB adapter (motor) — read paths only in v1 (plan §6.4).

There is no SQL to guard here, so the safety boundary is the *shape of the
API itself*: only `find` and `aggregate` are exposed, and the aggregation
pipeline is screened for the stages that turn a read into a write or an
arbitrary-code execution (`$out`, `$merge`, `$function`, `$where`,
`$accumulator`). `mapReduce` is simply never reachable.
"""

from __future__ import annotations

import time
from typing import Any

from app.datasources.base import (
    Column,
    ConnectionInfo,
    DbError,
    QueryResult,
    SchemaNamespace,
    Table,
)
from app.datasources.normalize import normalize_rows
from app.datasources.pool import get_mongo_client

#: Stages that write, execute JavaScript, or otherwise escape "this is a read".
#: `$where` and `$function` run server-side JS; `$out`/`$merge` write
#: collections; `$accumulator` is JS too. `$lookup` and `$graphLookup` are
#: allowed — they read, and refusing joins would make the tool useless.
BLOCKED_STAGES = frozenset(
    {
        "$out",
        "$merge",
        "$function",
        "$where",
        "$accumulator",
        # Cluster-wide introspection: other sessions' operations and queries.
        # The Mongo equivalent of reading pg_stat_activity.
        "$currentOp",
        "$listSessions",
        "$listLocalSessions",
        "$planCacheStats",
        "$listSampledQueries",
        "$querySettings",
        "$shardedDataDistribution",
    }
)

#: Same idea for a plain `find` filter — `$where` and `$expr`-wrapped JS.
BLOCKED_OPERATORS = frozenset({"$where", "$function", "$accumulator"})

#: Stages that read from a collection other than the one being aggregated.
#: A deny-list checked only against the tool's own `collection` argument is
#: bypassed by joining the denied collection in.
_JOIN_STAGES = frozenset({"$lookup", "$graphLookup"})

#: How many documents to sample when inferring a collection's shape. Mongo has
#: no schema, so introspection is genuinely an *inference*, and the tool says so.
SAMPLE_SIZE = 50


class MongoBlocked(DbError):
    """A pipeline or filter used something outside the read-only subset."""


def _walk(node: Any) -> list[str]:
    """Every operator key anywhere in a nested filter/pipeline."""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str) and key.startswith("$"):
                found.append(key)
            found.extend(_walk(value))
    elif isinstance(node, list | tuple):
        for item in node:
            found.extend(_walk(item))
    return found


def check_pipeline(pipeline: list[dict[str, Any]]) -> None:
    used = set(_walk(pipeline))
    bad = used & (BLOCKED_STAGES | BLOCKED_OPERATORS)
    if bad:
        raise MongoBlocked(f"disallowed aggregation stage(s): {', '.join(sorted(bad))}")


def check_filter(query: dict[str, Any]) -> None:
    bad = set(_walk(query)) & BLOCKED_OPERATORS
    if bad:
        raise MongoBlocked(f"disallowed operator(s): {', '.join(sorted(bad))}")


def referenced_collections(node: Any) -> list[str]:
    """Every collection a pipeline reads besides its own, found recursively —
    a `$lookup` can sit inside a `$facet`, or inside another lookup's
    sub-pipeline. Cross-database references are refused outright."""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _JOIN_STAGES and isinstance(value, dict):
                found.extend(_one_ref(value.get("from"), key))
            elif key == "$unionWith":
                target = value.get("coll") if isinstance(value, dict) else value
                if isinstance(value, dict) and "db" in value:
                    raise MongoBlocked("$unionWith across databases is not allowed")
                found.extend(_one_ref(target, key))
            found.extend(referenced_collections(value))
    elif isinstance(node, list | tuple):
        for item in node:
            found.extend(referenced_collections(item))
    return found


def _one_ref(target: Any, stage: str) -> list[str]:
    if target is None:
        return []  # a $lookup with only a sub-pipeline and no `from`
    if isinstance(target, str):
        return [target]
    # `from: {db, coll}` reaches into another database entirely.
    raise MongoBlocked(f"{stage} across databases is not allowed")


def check_collection(name: str, database: str, perms: Any) -> None:
    """Apply the connection's permission profile to one collection name.

    Deny matching is deliberately generous (exact, last dotted segment, or
    database-qualified); allow matching is deliberately strict (exact or
    database-qualified), because Mongo collection names may contain dots and
    "evil.orders" is not the "orders" an operator allowed.
    """
    lowered = (name or "").lower()
    if not lowered or "$" in lowered:
        raise MongoBlocked("a collection name is required")
    if lowered.startswith("system.") or ".system." in lowered:
        # system.js holds stored server-side JavaScript; system.profile
        # records other people's queries; system.users is credentials.
        raise MongoBlocked(f"{name} is a system collection and cannot be queried")
    qualified = f"{database.lower()}.{lowered}"
    if perms.deny_tables and {lowered, lowered.rsplit(".", 1)[-1], qualified} & set(
        perms.deny_tables
    ):
        raise MongoBlocked(f"collection {name} is denied for this connection")
    if perms.allow_tables and not ({lowered, qualified} & set(perms.allow_tables)):
        raise MongoBlocked(f"collection {name} is not in this connection's allowed tables")


def _require_positive(limit: int) -> None:
    """pymongo reads a limit of 0 as *no limit* and a negative one as "a single
    batch of |n|"; either would stream a whole collection into this process.
    Checked here, under the tool's own clamp, so no caller can send one."""
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise MongoBlocked("limit must be a positive whole number")


class MongoAdapter:
    engine = "mongodb"

    async def test(self, info: ConnectionInfo) -> None:
        try:
            client = await get_mongo_client(info)
            await client.admin.command("ping")
        except Exception as exc:
            raise DbError(str(exc)) from exc

    async def introspect(self, info: ConnectionInfo) -> list[SchemaNamespace]:
        """Infer each collection's fields by sampling. Types are whatever the
        sampled documents happened to hold — stated as inference, not truth."""
        try:
            client = await get_mongo_client(info)
            db = client[info.database]
            names = await db.list_collection_names()
            tables: list[Table] = []
            for name in sorted(names):
                if name.startswith("system."):
                    continue
                collection = db[name]
                approx = await collection.estimated_document_count()
                seen: dict[str, set[str]] = {}
                async for doc in collection.find({}, limit=SAMPLE_SIZE):
                    for key, value in doc.items():
                        seen.setdefault(str(key), set()).add(type(value).__name__)
                tables.append(
                    Table(
                        name=name,
                        approx_rows=int(approx),
                        columns=[
                            Column(name=k, type="|".join(sorted(v)), nullable=True, pk=(k == "_id"))
                            for k, v in sorted(seen.items())
                        ],
                    )
                )
        except Exception as exc:
            raise DbError(str(exc)) from exc
        return [SchemaNamespace(name=info.database, tables=tables)]

    async def find(
        self,
        info: ConnectionInfo,
        *,
        collection: str,
        query: dict[str, Any],
        projection: dict[str, Any] | None,
        limit: int,
        timeout_ms: int,
    ) -> QueryResult:
        _require_positive(limit)
        check_filter(query)
        # A find projection accepts aggregation expressions (MongoDB 4.4+),
        # so `{"x": {"$function": ...}}` runs server-side JavaScript just as
        # it would in a filter. Only `query` used to be checked.
        check_filter(projection or {})
        started = time.perf_counter()
        try:
            client = await get_mongo_client(info)
            cursor = client[info.database][collection].find(
                query, projection or None, limit=limit, max_time_ms=timeout_ms
            )
            docs = [d async for d in cursor]
        except MongoBlocked:
            raise
        except Exception as exc:
            raise DbError(str(exc)) from exc
        return self._to_result(docs, limit, started)

    async def aggregate(
        self,
        info: ConnectionInfo,
        *,
        collection: str,
        pipeline: list[dict[str, Any]],
        limit: int,
        timeout_ms: int,
    ) -> QueryResult:
        _require_positive(limit)
        check_pipeline(pipeline)
        started = time.perf_counter()
        # Append rather than trust the caller's own $limit: a pipeline can
        # $unwind its way well past whatever limit it declared earlier.
        capped = [*pipeline, {"$limit": limit}]
        try:
            client = await get_mongo_client(info)
            cursor = client[info.database][collection].aggregate(capped, maxTimeMS=timeout_ms)
            docs = [d async for d in cursor]
        except MongoBlocked:
            raise
        except Exception as exc:
            raise DbError(str(exc)) from exc
        return self._to_result(docs, limit, started)

    @staticmethod
    def _to_result(docs: list[dict[str, Any]], max_rows: int, started: float) -> QueryResult:
        elapsed = int((time.perf_counter() - started) * 1000)
        # Documents are heterogeneous, so the column set is the union of keys
        # in the order first seen — stable, and honest about ragged data.
        columns: list[str] = []
        for doc in docs:
            for key in doc:
                if key not in columns:
                    columns.append(str(key))
        rows, truncated = normalize_rows(docs, columns, max_rows=max_rows)
        return QueryResult(
            columns=columns,
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
            elapsed_ms=elapsed,
        )


__all__ = [
    "BLOCKED_OPERATORS",
    "BLOCKED_STAGES",
    "SAMPLE_SIZE",
    "MongoAdapter",
    "MongoBlocked",
    "check_collection",
    "check_filter",
    "check_pipeline",
    "referenced_collections",
]
