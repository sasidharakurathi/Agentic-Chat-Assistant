"""Connection pools for tenants' databases (plan §6.1).

One pool per `db_connection`, created lazily, small, and evicted when idle.
The shape is dictated by what this actually is: **many** tenant databases,
each used in bursts (a few queries during a chat turn, then nothing for
hours). A permanently-open pool per connection would exhaust the *tenant's*
`max_connections` long before it exhausted ours — so pools are tiny, and a
global cap stops one busy org from starving every other.

Keyed by the connection's identity rather than its row id, so rotating a
password or changing a host opens a fresh pool instead of silently reusing
one built with stale credentials.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from app.datasources.base import ConnectionInfo, DbError
from app.logging import get_logger

log = get_logger(__name__)

#: Per tenant database. Deliberately small — see the module docstring.
POOL_MIN = 0
POOL_MAX = 4

#: Across every tenant database this process holds open.
GLOBAL_MAX_POOLS = 32

#: Pools unused for this long are closed on the next acquire.
IDLE_TTL_SECONDS = 300

#: Opening a connection must not hang a chat turn.
CONNECT_TIMEOUT_SECONDS = 10.0

#: Every statement timeout is enforced by the *database*; the client waits
#: this much longer before giving up itself. The server's limit must fire
#: first (it actually stops the work); the client deadline is the backstop
#: for a server that is hung or ignoring the limit.
CLIENT_GRACE_SECONDS = 1.0


@dataclass
class _Entry:
    pool: Any
    last_used: float
    closer: Any


_pools: dict[str, _Entry] = {}
_lock = asyncio.Lock()


def _key(info: ConnectionInfo) -> str:
    """Identity, including every credential — so a rotated credential never
    silently keeps using a pool opened with the old one, and two connections
    never share one authenticated client.

    `options` is part of it because for MongoDB the whole connection string
    (credentials included) arrives there. It used to be left out, so two
    connections differing only in their URI got the first one's client —
    and with it the first one's login. Credentials enter the key as a digest,
    never verbatim: keys live in process memory and can reach a log line.
    """
    options = hashlib.sha256(
        json.dumps(info.options or {}, sort_keys=True, default=str).encode()
    ).hexdigest()[:24]
    return "|".join(
        str(x)
        for x in (
            info.engine,
            info.host,
            info.port,
            info.database,
            info.username,
            hash(info.password or ""),
            options,
            sorted(info.ssl.items()) if info.ssl else "",
        )
    )


async def _evict_idle(now: float) -> None:
    stale = [k for k, e in _pools.items() if now - e.last_used > IDLE_TTL_SECONDS]
    for k in stale:
        entry = _pools.pop(k)
        try:
            await entry.closer(entry.pool)
        except Exception:  # pragma: no cover - best effort
            log.warning("pool_close_failed")


async def _evict_oldest_if_full() -> None:
    while len(_pools) >= GLOBAL_MAX_POOLS:
        oldest = min(_pools, key=lambda k: _pools[k].last_used)
        entry = _pools.pop(oldest)
        try:
            await entry.closer(entry.pool)
        except Exception:  # pragma: no cover - best effort
            log.warning("pool_close_failed")


async def _get_or_create(info: ConnectionInfo, factory: Any, closer: Any) -> Any:
    now = time.monotonic()
    async with _lock:
        await _evict_idle(now)
        key = _key(info)
        entry = _pools.get(key)
        if entry is None:
            await _evict_oldest_if_full()
            try:
                pool = await asyncio.wait_for(factory(info), timeout=CONNECT_TIMEOUT_SECONDS)
            except TimeoutError as exc:
                raise DbError(
                    f"timed out connecting to {info.engine} at {info.host}:{info.port}"
                ) from exc
            entry = _Entry(pool=pool, last_used=now, closer=closer)
            _pools[key] = entry
        entry.last_used = now
        return entry.pool


# ── postgres ─────────────────────────────────────────────────


async def _pg_factory(info: ConnectionInfo) -> Any:
    import asyncpg

    ssl_mode = info.ssl.get("mode") if info.ssl else None
    return await asyncpg.create_pool(
        host=info.host,
        port=info.port or 5432,
        database=info.database,
        user=info.username,
        password=info.password,
        min_size=POOL_MIN,
        max_size=POOL_MAX,
        ssl=ssl_mode or None,
        # Tenant databases are not ours to keep sessions warm in.
        max_inactive_connection_lifetime=IDLE_TTL_SECONDS,
        # asyncpg caches prepared statements per connection; with pgbouncer in
        # transaction mode (common in managed Postgres) that breaks loudly.
        statement_cache_size=0,
        command_timeout=30,
    )


async def _pg_closer(pool: Any) -> None:
    await pool.close()


@asynccontextmanager
async def acquire_postgres(info: ConnectionInfo) -> AsyncIterator[Any]:
    pool = await _get_or_create(info, _pg_factory, _pg_closer)
    async with pool.acquire() as conn:
        yield conn


# ── mysql ────────────────────────────────────────────────────


async def _mysql_factory(info: ConnectionInfo) -> Any:
    import aiomysql

    return await aiomysql.create_pool(
        host=info.host or "localhost",
        port=info.port or 3306,
        db=info.database,
        user=info.username,
        password=info.password or "",
        minsize=POOL_MIN,
        maxsize=POOL_MAX,
        autocommit=True,
        connect_timeout=CONNECT_TIMEOUT_SECONDS,
    )


async def _mysql_closer(pool: Any) -> None:
    pool.close()
    await pool.wait_closed()


@asynccontextmanager
async def acquire_mysql(info: ConnectionInfo) -> AsyncIterator[Any]:
    pool = await _get_or_create(info, _mysql_factory, _mysql_closer)
    async with pool.acquire() as conn:
        yield conn


# ── mongodb ──────────────────────────────────────────────────


async def _mongo_factory(info: ConnectionInfo) -> Any:
    from motor.motor_asyncio import AsyncIOMotorClient

    if info.options.get("uri"):
        uri = str(info.options["uri"])
    else:
        auth = ""
        if info.username:
            from urllib.parse import quote_plus

            auth = f"{quote_plus(info.username)}:{quote_plus(info.password or '')}@"
        uri = f"mongodb://{auth}{info.host or 'localhost'}:{info.port or 27017}"
    # motor pools internally; one client per connection is the analogue.
    return AsyncIOMotorClient(
        uri,
        maxPoolSize=POOL_MAX,
        serverSelectionTimeoutMS=int(CONNECT_TIMEOUT_SECONDS * 1000),
    )


async def _mongo_closer(client: Any) -> None:
    client.close()


async def get_mongo_client(info: ConnectionInfo) -> Any:
    return await _get_or_create(info, _mongo_factory, _mongo_closer)


# ── lifecycle ────────────────────────────────────────────────


async def close_all() -> None:
    """Shut every pool down — called on app shutdown and between tests."""
    async with _lock:
        for entry in list(_pools.values()):
            try:
                await entry.closer(entry.pool)
            except Exception:  # pragma: no cover - best effort
                log.warning("pool_close_failed")
        _pools.clear()


def open_pool_count() -> int:
    return len(_pools)


__all__ = [
    "CONNECT_TIMEOUT_SECONDS",
    "GLOBAL_MAX_POOLS",
    "IDLE_TTL_SECONDS",
    "POOL_MAX",
    "acquire_mysql",
    "acquire_postgres",
    "close_all",
    "get_mongo_client",
    "open_pool_count",
]
