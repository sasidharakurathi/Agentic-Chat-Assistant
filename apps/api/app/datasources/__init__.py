"""Tenant database integrations (plan §6).

`get_adapter(engine)` is the only entry point above this package — everything
else here is per-engine detail.
"""

from app.datasources.base import (
    Column,
    ConnectionInfo,
    DbAdapter,
    DbError,
    QueryResult,
    SchemaNamespace,
    SqlAdapter,
    Table,
)
from app.datasources.mongodb import MongoAdapter, MongoBlocked
from app.datasources.mysql import MysqlAdapter
from app.datasources.postgres import PostgresAdapter
from app.datasources.sqlite import SqliteAdapter

#: Engines that speak SQL. Mongo is deliberately not here — see `SqlAdapter`.
_SQL_ADAPTERS: dict[str, SqlAdapter] = {
    "postgres": PostgresAdapter(),
    "mysql": MysqlAdapter(),
    "sqlite": SqliteAdapter(),
}

_MONGO = MongoAdapter()

SQL_ENGINES = frozenset(_SQL_ADAPTERS)


def get_adapter(engine: str) -> DbAdapter:
    """Any engine — enough to test a connection or introspect it."""
    if engine == "mongodb":
        return _MONGO
    return get_sql_adapter(engine)


def get_sql_adapter(engine: str) -> SqlAdapter:
    """SQL engines only. Asking for mongodb here is the caller's bug."""
    try:
        return _SQL_ADAPTERS[engine]
    except KeyError as exc:
        raise DbError(f"{engine!r} does not support SQL statements") from exc


def get_mongo_adapter() -> MongoAdapter:
    return _MONGO


__all__ = [
    "SQL_ENGINES",
    "Column",
    "ConnectionInfo",
    "DbAdapter",
    "DbError",
    "MongoAdapter",
    "MongoBlocked",
    "MysqlAdapter",
    "PostgresAdapter",
    "QueryResult",
    "SchemaNamespace",
    "SqlAdapter",
    "SqliteAdapter",
    "Table",
    "get_adapter",
    "get_mongo_adapter",
    "get_sql_adapter",
]
