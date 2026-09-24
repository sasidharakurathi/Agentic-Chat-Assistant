"""Database connection lifecycle (tasks 3.2 / 3.3 / 3.7).

Owns the one path where a plaintext credential exists in memory:
`connection_info()` decrypts it, hands it to an adapter, and lets it fall out
of scope. Nothing else in the app ever sees it, and no response model can
carry it back out.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import BadRequest, NotFound
from app.config import settings
from app.datasources import ConnectionInfo, DbError, get_adapter
from app.datasources.base import SchemaNamespace
from app.datasources.sql_guard import Permissions
from app.db.pagination import PageResult, keyset_page
from app.logging import get_logger
from app.models.assistant import Assistant
from app.models.integration import (
    DbConnection,
    DbConnectionStatus,
    DbEngine,
    DbSchemaCache,
)
from app.models.secret import Secret, SecretKind
from app.queue import enqueue_schema_refresh
from app.schemas.db_connection import (
    CREDENTIAL_OPTION_KEYS,
    DbConnectionCreate,
    DbConnectionSummary,
    DbConnectionUpdate,
    DbSchemaOut,
    DbTestResult,
)
from app.security.crypto import CryptoError, SealedSecret, open_sealed, seal
from app.services import audit

log = get_logger(__name__)


def to_summary(conn: DbConnection) -> DbConnectionSummary:
    return DbConnectionSummary.model_validate(conn).model_copy(
        update={
            "has_password": conn.secret_ref is not None,
            "has_connection_uri": conn.uri_secret_ref is not None,
            # Belt and braces. The schemas refuse credential keys in `options`
            # on the way in; this makes sure a row written before that rule
            # existed cannot echo one on the way out.
            "options": {
                k: v
                for k, v in (conn.options or {}).items()
                if str(k).lower() not in CREDENTIAL_OPTION_KEYS
            },
        }
    )


async def _store_secret(
    session: AsyncSession, *, org_id: uuid.UUID, value: str, kind: SecretKind
) -> uuid.UUID:
    sealed = seal(value, kind=kind.value)
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
    """Delete a secret row that nothing references any more.

    Replacing a credential used to leave the old ciphertext behind, on the
    theory that an in-flight query might still need it. It cannot: an
    in-flight query already holds the decrypted value. All the leftover row
    did was accumulate unreferenced ciphertext.
    """
    if secret_ref is None:
        return
    row = await session.get(Secret, secret_ref)
    if row is not None:
        await session.delete(row)


async def _read_password(session: AsyncSession, secret_ref: uuid.UUID | None) -> str | None:
    if secret_ref is None:
        return None
    row = await session.get(Secret, secret_ref)
    if row is None:
        return None
    try:
        return open_sealed(
            SealedSecret(ciphertext=row.ciphertext, dek_wrapped=row.dek_wrapped, nonce=row.nonce),
            kind=row.kind.value,
        )
    except CryptoError:
        # Almost always a KEK mismatch after a bad deploy. Say so without
        # leaking whether the row exists or what it holds.
        log.error("secret_decrypt_failed", secret_id=str(secret_ref))
        raise BadRequest(
            "stored credential could not be decrypted (APP_KEK may have changed)",
            code="secret_undecryptable",
        ) from None


async def connection_info(session: AsyncSession, conn: DbConnection) -> ConnectionInfo:
    """Build the adapter's input, decrypting credentials at the last moment.

    This is the only place a sealed connection string is ever opened, and it
    goes straight into the adapter's `options` — never back into the row."""
    options = dict(conn.options or {})
    if conn.uri_secret_ref is not None:
        options["uri"] = await _read_password(session, conn.uri_secret_ref)
    return ConnectionInfo(
        engine=conn.engine.value,
        database=conn.database,
        host=conn.host,
        port=conn.port,
        username=conn.username,
        password=await _read_password(session, conn.secret_ref),
        options=options,
        ssl=dict(conn.ssl or {}),
    )


# ── CRUD ─────────────────────────────────────────────────────


async def create(
    session: AsyncSession,
    *,
    assistant: Assistant,
    body: DbConnectionCreate,
    user_id: uuid.UUID,
    ip: str | None = None,
) -> DbConnection:
    secret_ref = None
    if body.password:
        secret_ref = await _store_secret(
            session, org_id=assistant.org_id, value=body.password, kind=SecretKind.db_password
        )
    uri_secret_ref = None
    if body.connection_uri:
        uri_secret_ref = await _store_secret(
            session,
            org_id=assistant.org_id,
            value=body.connection_uri,
            kind=SecretKind.db_connection_uri,
        )

    conn = DbConnection(
        assistant_id=assistant.id,
        org_id=assistant.org_id,
        name=body.name.strip(),
        engine=DbEngine(body.engine),
        host=body.host,
        port=body.port,
        database=body.database,
        username=body.username,
        secret_ref=secret_ref,
        uri_secret_ref=uri_secret_ref,
        options=body.options,
        ssl=body.ssl,
        permissions=body.permissions.model_dump(),
        status=DbConnectionStatus.unknown,
    )
    session.add(conn)
    await session.flush()
    await audit.record(
        session,
        action="db_connection.create",
        org_id=assistant.org_id,
        actor_user_id=user_id,
        target_type="db_connection",
        target_id=conn.id,
        # Deliberately records engine/host, never the credential.
        meta={"engine": body.engine, "host": body.host},
        ip=ip,
    )
    await session.commit()
    # Warm the schema cache in the background, so the agent's first
    # introspection is not also the first time anyone looked.
    await enqueue_schema_refresh(conn.id)
    return conn


async def list_for_assistant(session: AsyncSession, assistant_id: uuid.UUID) -> list[DbConnection]:
    """Every row, unpaged, for the model's `sql_list_schemas` tool, which needs the
    whole catalogue. The API lists through `page_for_assistant`."""
    rows = await session.scalars(
        select(DbConnection)
        .where(DbConnection.assistant_id == assistant_id)
        .order_by(DbConnection.created_at.desc(), DbConnection.id.desc())
    )
    return list(rows)


async def page_for_assistant(
    session: AsyncSession, assistant_id: uuid.UUID, *, limit: int = 50, cursor: str | None = None
) -> PageResult[DbConnection]:
    return await keyset_page(
        session,
        select(DbConnection).where(DbConnection.assistant_id == assistant_id),
        [DbConnection.created_at, DbConnection.id],
        limit=limit,
        cursor=cursor,
    )


async def get(
    session: AsyncSession, *, assistant_id: uuid.UUID, connection_id: uuid.UUID
) -> DbConnection:
    conn = await session.scalar(
        select(DbConnection).where(
            DbConnection.id == connection_id, DbConnection.assistant_id == assistant_id
        )
    )
    if conn is None:
        raise NotFound("Database connection not found")
    return conn


async def update(
    session: AsyncSession,
    conn: DbConnection,
    body: DbConnectionUpdate,
    *,
    user_id: uuid.UUID,
    ip: str | None = None,
) -> DbConnection:
    data = body.model_dump(exclude_unset=True)
    password = data.pop("password", None)
    connection_uri = data.pop("connection_uri", None)
    permissions = data.pop("permissions", None)
    if connection_uri and conn.engine != DbEngine.mongodb:
        raise BadRequest(
            "connection_uri is only for mongodb; use host/port/username/password",
            code="connection_uri_not_supported",
        )

    for field, value in data.items():
        if value is not None:
            setattr(conn, field, value)
    if permissions is not None:
        conn.permissions = permissions
    if password:
        old, conn.secret_ref = (
            conn.secret_ref,
            await _store_secret(
                session, org_id=conn.org_id, value=password, kind=SecretKind.db_password
            ),
        )
        await _drop_secret(session, old)
    if connection_uri:
        old, conn.uri_secret_ref = (
            conn.uri_secret_ref,
            await _store_secret(
                session, org_id=conn.org_id, value=connection_uri, kind=SecretKind.db_connection_uri
            ),
        )
        await _drop_secret(session, old)
    # Anything changed here can invalidate reachability.
    conn.status = DbConnectionStatus.unknown
    conn.error = None
    # ...and the cached schema. It holds only the tables the *old*
    # permissions allowed, so it cannot be re-filtered in either direction:
    # a newly denied table stayed visible to the model until someone pressed
    # refresh, and a newly allowed one stayed missing. A new host, database
    # or credential can point at a different schema altogether. Only a
    # rename leaves it valid.
    if permissions is not None or password or connection_uri or set(data) - {"name"}:
        await _drop_schema_cache(session, conn)
        refresh_after_commit = True
    else:
        refresh_after_commit = False

    await audit.record(
        session,
        action="db_connection.update",
        org_id=conn.org_id,
        actor_user_id=user_id,
        target_type="db_connection",
        target_id=conn.id,
        # Field *names* only: which credential changed, never its value.
        meta={
            "fields": sorted(data)
            + (["password"] if password else [])
            + (["connection_uri"] if connection_uri else [])
        },
        ip=ip,
    )
    await session.commit()
    if refresh_after_commit:
        await enqueue_schema_refresh(conn.id)
    return conn


async def delete(
    session: AsyncSession, conn: DbConnection, *, user_id: uuid.UUID, ip: str | None = None
) -> None:
    await audit.record(
        session,
        action="db_connection.delete",
        org_id=conn.org_id,
        actor_user_id=user_id,
        target_type="db_connection",
        target_id=conn.id,
        ip=ip,
    )
    await _drop_secret(session, conn.secret_ref)
    await _drop_secret(session, conn.uri_secret_ref)
    await session.delete(conn)
    await session.commit()


# ── reachability + introspection ─────────────────────────────


async def test_connection(session: AsyncSession, conn: DbConnection) -> DbTestResult:
    info = await connection_info(session, conn)
    adapter = get_adapter(conn.engine.value)
    started = time.perf_counter()
    try:
        await adapter.test(info)
    except DbError as exc:
        conn.status = DbConnectionStatus.error
        conn.error = str(exc)[:2000]
        conn.last_checked_at = datetime.now(UTC)
        await session.commit()
        return DbTestResult(ok=False, error=conn.error)

    conn.status = DbConnectionStatus.ok
    conn.error = None
    conn.last_checked_at = datetime.now(UTC)
    await session.commit()
    return DbTestResult(ok=True, elapsed_ms=int((time.perf_counter() - started) * 1000))


def filter_schema(
    namespaces: list[SchemaNamespace], perms: Permissions
) -> tuple[list[dict[str, Any]], bool]:
    """Apply the permission profile to introspection output.

    Denied tables are dropped **here**, before anything is cached or
    returned — so a table the operator excluded never lands in the schema
    cache, never reaches the model's context, and cannot be discovered by
    reading a stale row.
    """
    filtered = False
    out: list[dict[str, Any]] = []
    for ns in namespaces:
        tables = []
        for table in ns.tables:
            bare = table.name.lower()
            qualified = f"{ns.name.lower()}.{bare}"
            if perms.deny_tables and (bare in perms.deny_tables or qualified in perms.deny_tables):
                filtered = True
                continue
            if perms.allow_tables and not (
                bare in perms.allow_tables or qualified in perms.allow_tables
            ):
                filtered = True
                continue
            tables.append(
                {
                    "name": table.name,
                    "approx_rows": table.approx_rows,
                    "columns": [
                        {
                            "name": c.name,
                            "type": c.type,
                            "nullable": c.nullable,
                            "pk": c.pk,
                            "fk": c.fk,
                        }
                        for c in table.columns
                    ],
                }
            )
        if tables:
            out.append({"name": ns.name, "tables": tables})
    return out, filtered


async def refresh_schema(session: AsyncSession, conn: DbConnection) -> DbSchemaOut:
    info = await connection_info(session, conn)
    adapter = get_adapter(conn.engine.value)
    try:
        namespaces = await adapter.introspect(info)
    except DbError as exc:
        conn.status = DbConnectionStatus.error
        conn.error = str(exc)[:2000]
        await session.commit()
        raise BadRequest(f"introspection failed: {exc}", code="introspection_failed") from exc

    perms = Permissions.from_dict(conn.permissions)
    schemas, filtered = filter_schema(namespaces, perms)

    row = await session.scalar(
        select(DbSchemaCache).where(DbSchemaCache.db_connection_id == conn.id)
    )
    now = datetime.now(UTC)
    # `filtered` is stored with the tables: the "some tables are hidden by
    # permissions" hint used to be lost on the next read.
    cache = {"schemas": schemas, "filtered": filtered}
    if row is None:
        row = DbSchemaCache(
            db_connection_id=conn.id,
            org_id=conn.org_id,
            db_schema=cache,
            refreshed_at=now,
        )
        session.add(row)
    else:
        row.db_schema = cache
        row.refreshed_at = now

    conn.status = DbConnectionStatus.ok
    conn.error = None
    conn.last_checked_at = now
    await session.commit()
    return DbSchemaOut(schemas=schemas, refreshed_at=now, filtered=filtered)


async def cached_schema(session: AsyncSession, conn: DbConnection) -> DbSchemaOut:
    row = await session.scalar(
        select(DbSchemaCache).where(DbSchemaCache.db_connection_id == conn.id)
    )
    if row is None:
        return DbSchemaOut(schemas=[], refreshed_at=None)
    stored = row.db_schema or {}
    return DbSchemaOut(
        schemas=list(stored.get("schemas", [])),
        refreshed_at=row.refreshed_at,
        filtered=bool(stored.get("filtered", False)),
    )


def schema_is_stale(cached: DbSchemaOut, *, now: datetime | None = None) -> bool:
    """No cache, or older than `SCHEMA_CACHE_TTL_S`."""
    if cached.refreshed_at is None:
        return True
    age = (now or datetime.now(UTC)) - cached.refreshed_at
    return age.total_seconds() > settings.schema_cache_ttl_s


async def _drop_schema_cache(session: AsyncSession, conn: DbConnection) -> None:
    row = await session.scalar(
        select(DbSchemaCache).where(DbSchemaCache.db_connection_id == conn.id)
    )
    if row is not None:
        await session.delete(row)


__all__ = [
    "cached_schema",
    "connection_info",
    "create",
    "delete",
    "filter_schema",
    "get",
    "list_for_assistant",
    "page_for_assistant",
    "refresh_schema",
    "schema_is_stale",
    "test_connection",
    "to_summary",
    "update",
]
