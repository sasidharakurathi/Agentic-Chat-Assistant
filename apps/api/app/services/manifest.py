"""What an approved version uses, frozen (Phase 7b.1, plan D16).

A version's config names what it uses: knowledge sources, database
connections, MCP servers and their tools. Those live outside the version
and can change after an admin approves it: a document reindexed, a
connection pointed at another database or given write access, an MCP tool
whose description now says read-only. Without more, an approval would not
stop a builder changing what employees get.

So each version records a **manifest** when it is submitted:

- **knowledge**: the documents its sources held, each by id. A reindex
  keeps the old document (marked superseded) while a version still names
  it, so Chat goes on answering from the copy that was approved; the new
  copy is for previews until the next approval (decided 2026-10-08).
- **databases**: a fingerprint of each connection: engine, host, port,
  database, user, which secret, options, TLS and permissions.
- **MCP servers**: a fingerprint of each server (transport, command, URL,
  header and environment names, which secrets, sandbox) and of each tool it
  is allowed (description, input schema, read-only hint).
- **HTTP connections** (Phase 9.3): a fingerprint of each one its HTTP
  actions call (base URL, header names, which secret, whether it says who
  is asking).

A turn on a version checks the live resources against its manifest and
fails closed: a connection, server or tool that has changed since is not
offered (`apply`). Approving, and rolling back, refuse while anything has
drifted. Each run records the manifest's hash.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging import get_logger
from app.models.assistant import Assistant, AssistantVersion, VersionStatus
from app.models.integration import DbConnection, HttpConnection, McpServer
from app.models.rag import DataSource, Document
from app.schemas.assistant_config import AssistantConfig
from app.services import audit

log = get_logger(__name__)

#: Bumped if the shape changes, so an old manifest is read as what it is.
MANIFEST_VERSION = 1

#: Versions whose manifests can keep documents alive. Of the approved ones,
#: only the live version and the one approved before it do (`kept_documents`).
KEEPS = (VersionStatus.submitted, VersionStatus.approved)


def _digest(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _ref(value: uuid.UUID | None) -> str | None:
    return str(value) if value is not None else None


def connection_fingerprint(conn: DbConnection) -> str:
    """Everything that decides which database is reached, as whom, and what
    may be done there. A rotated password under the same secret is not a
    change: the secret's id is fingerprinted, not its value."""
    return _digest(
        {
            "engine": conn.engine.value,
            "host": conn.host,
            "port": conn.port,
            "database": conn.database,
            "username": conn.username,
            "secret_ref": _ref(conn.secret_ref),
            "uri_secret_ref": _ref(conn.uri_secret_ref),
            "options": conn.options,
            "ssl": conn.ssl,
            "permissions": conn.permissions,
        }
    )


def server_fingerprint(server: McpServer) -> str:
    return _digest(
        {
            "transport": server.transport.value,
            "command": server.command,
            "args": server.args,
            "url": server.url,
            "header_names": sorted(server.header_names or []),
            "headers_secret_ref": _ref(server.headers_secret_ref),
            "env_keys": sorted(server.env_keys or []),
            "env_secret_ref": _ref(server.env_secret_ref),
            "sandbox": server.sandbox,
        }
    )


def http_fingerprint(conn: HttpConnection) -> str:
    """Where an HTTP action goes, with which credential, and whether the
    system is told who is asking. Its name is a label, not part of it."""
    return _digest(
        {
            "base_url": conn.base_url,
            "header_names": sorted(conn.header_names or []),
            "headers_secret_ref": _ref(conn.headers_secret_ref),
            "send_asker": conn.send_asker,
        }
    )


def tool_fingerprint(spec: dict[str, Any]) -> str:
    """What the model is told about a tool, and whether it asks first."""
    return _digest(
        {
            "description": str(spec.get("description") or ""),
            "input_schema": spec.get("input_schema"),
            "read_only": spec.get("read_only") is True,
        }
    )


def digest(manifest: dict[str, Any]) -> str:
    return _digest(manifest)


# ── recording ────────────────────────────────────────────────


async def _knowledge(
    session: AsyncSession, assistant_id: uuid.UUID, config: AssistantConfig
) -> dict[str, Any]:
    if not config.rag.enabled:
        return {"sources": [], "documents": []}
    stmt = select(DataSource.id, DataSource.name).where(DataSource.assistant_id == assistant_id)
    if config.rag.source_ids:
        named = [u for u in (_uuid(s) for s in config.rag.source_ids) if u is not None]
        stmt = stmt.where(DataSource.id.in_(named))
    sources = sorted((await session.execute(stmt)).all(), key=lambda r: str(r[0]))
    documents = (
        await session.execute(
            select(Document.id, Document.data_source_id, Document.checksum).where(
                Document.data_source_id.in_([s[0] for s in sources]),
                Document.superseded_at.is_(None),
            )
        )
    ).all()
    return {
        "sources": [{"id": str(i), "name": name} for i, name in sources],
        "documents": sorted(
            ({"id": str(i), "source_id": str(s), "checksum": c} for i, s, c in documents),
            key=lambda d: d["id"],
        ),
    }


async def build(
    session: AsyncSession, assistant_id: uuid.UUID, config: AssistantConfig
) -> dict[str, Any]:
    """The manifest of `config` as things stand now."""
    databases = []
    for ref in config.databases:
        cid = _uuid(ref.connection_id)
        conn = await session.get(DbConnection, cid) if cid else None
        if conn is not None and conn.assistant_id == assistant_id:
            databases.append(
                {"id": str(conn.id), "name": conn.name, "fingerprint": connection_fingerprint(conn)}
            )
    servers = []
    for mref in config.mcp_servers:
        sid = _uuid(mref.id)
        server = await session.get(McpServer, sid) if sid else None
        if server is None or server.assistant_id != assistant_id:
            continue
        listed = {str(t.get("name")): t for t in server.tools or []}
        servers.append(
            {
                "id": str(server.id),
                "name": server.name,
                "fingerprint": server_fingerprint(server),
                "tools": {
                    name: tool_fingerprint(listed[name])
                    for name in sorted(mref.tools)
                    if name in listed
                },
            }
        )
    http = []
    for http_id in sorted({a.connection_id for a in config.tools.http_request.actions}):
        hid = _uuid(http_id)
        hconn = await session.get(HttpConnection, hid) if hid else None
        if hconn is not None and hconn.assistant_id == assistant_id:
            http.append(
                {"id": str(hconn.id), "name": hconn.name, "fingerprint": http_fingerprint(hconn)}
            )
    return {
        "v": MANIFEST_VERSION,
        "knowledge": await _knowledge(session, assistant_id, config),
        "databases": sorted(databases, key=lambda d: d["id"]),
        "mcp_servers": sorted(servers, key=lambda s: s["id"]),
        "http_connections": sorted(http, key=lambda h: h["id"]),
    }


async def record(
    session: AsyncSession, version: AssistantVersion, config: AssistantConfig
) -> dict[str, Any]:
    version.manifest = await build(session, version.assistant_id, config)
    version.manifest_hash = digest(version.manifest)
    return version.manifest


# ── checking ─────────────────────────────────────────────────


@dataclass(frozen=True)
class Drift:
    """One thing a version uses that is not as it was when submitted."""

    kind: Literal["database", "mcp_server", "mcp_tool", "http_connection", "document"]
    id: str
    name: str
    what: Literal["changed", "missing"]
    tool: str | None = None

    def describe(self) -> str:
        if self.kind == "database":
            return f"Database connection '{self.name}' has {self._verb()} since it was approved"
        if self.kind == "mcp_server":
            return f"MCP server '{self.name}' has {self._verb()} since it was approved"
        if self.kind == "mcp_tool":
            return f"Tool '{self.tool}' on MCP server '{self.name}' has {self._verb()}"
        if self.kind == "http_connection":
            return f"HTTP connection '{self.name}' has {self._verb()} since it was approved"
        return f"A document from '{self.name}' has been deleted"

    def _verb(self) -> str:
        return "changed" if self.what == "changed" else "been deleted"

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "id": self.id,
            "name": self.name,
            "what": self.what,
            "tool": self.tool,
            "message": self.describe(),
        }


async def _http_drift(
    session: AsyncSession, assistant_id: uuid.UUID, manifest: dict[str, Any]
) -> list[Drift]:
    out: list[Drift] = []
    for item in manifest.get("http_connections", []):
        hconn = await session.get(HttpConnection, _uuid(item["id"]))
        if hconn is None or hconn.assistant_id != assistant_id:
            out.append(Drift("http_connection", item["id"], item["name"], "missing"))
        elif http_fingerprint(hconn) != item["fingerprint"]:
            out.append(Drift("http_connection", item["id"], item["name"], "changed"))
    return out


async def check(
    session: AsyncSession, assistant_id: uuid.UUID, manifest: dict[str, Any]
) -> list[Drift]:
    """What has changed since `manifest` was recorded."""
    out: list[Drift] = []
    for db in manifest.get("databases", []):
        conn = await session.get(DbConnection, _uuid(db["id"]))
        if conn is None or conn.assistant_id != assistant_id:
            out.append(Drift("database", db["id"], db["name"], "missing"))
        elif connection_fingerprint(conn) != db["fingerprint"]:
            out.append(Drift("database", db["id"], db["name"], "changed"))
    for srv in manifest.get("mcp_servers", []):
        server = await session.get(McpServer, _uuid(srv["id"]))
        if server is None or server.assistant_id != assistant_id:
            out.append(Drift("mcp_server", srv["id"], srv["name"], "missing"))
            continue
        if server_fingerprint(server) != srv["fingerprint"]:
            out.append(Drift("mcp_server", srv["id"], srv["name"], "changed"))
            continue
        listed = {str(t.get("name")): t for t in server.tools or []}
        for tool, fingerprint in srv.get("tools", {}).items():
            if tool not in listed:
                out.append(Drift("mcp_tool", srv["id"], srv["name"], "missing", tool))
            elif tool_fingerprint(listed[tool]) != fingerprint:
                out.append(Drift("mcp_tool", srv["id"], srv["name"], "changed", tool))
    out.extend(await _http_drift(session, assistant_id, manifest))
    knowledge = manifest.get("knowledge", {})
    named = [d["id"] for d in knowledge.get("documents", [])]
    if named:
        present = {
            str(i)
            for i in await session.scalars(
                select(Document.id).where(Document.id.in_([_uuid(d) for d in named]))
            )
        }
        names = {s["id"]: s["name"] for s in knowledge.get("sources", [])}
        for doc in knowledge["documents"]:
            if doc["id"] not in present:
                source = doc["source_id"]
                out.append(Drift("document", doc["id"], names.get(source, source), "missing"))
    return out


# ── using ────────────────────────────────────────────────────


@dataclass(frozen=True)
class Frozen:
    """What a turn on a version may use."""

    manifest_hash: str
    #: The knowledge it was approved with: retrieval searches only these.
    documents: frozenset[uuid.UUID]
    drift: tuple[Drift, ...]


def apply(
    config: AssistantConfig, manifest: dict[str, Any], drift: Iterable[Drift]
) -> AssistantConfig:
    """The config a turn runs with: anything that drifted is left out, and
    knowledge is narrowed to the sources the version was approved with."""
    drift = list(drift)
    cfg = config.model_copy(deep=True)
    gone_db = {d.id for d in drift if d.kind == "database"}
    cfg.databases = [d for d in cfg.databases if d.connection_id not in gone_db]
    gone_servers = {d.id for d in drift if d.kind == "mcp_server"}
    gone_tools = {(d.id, d.tool) for d in drift if d.kind == "mcp_tool"}
    frozen_tools = {s["id"]: set(s.get("tools", {})) for s in manifest.get("mcp_servers", [])}
    servers = []
    for ref in cfg.mcp_servers:
        if ref.id in gone_servers or ref.id not in frozen_tools:
            continue
        ref.tools = [
            t for t in ref.tools if t in frozen_tools[ref.id] and (ref.id, t) not in gone_tools
        ]
        servers.append(ref)
    cfg.mcp_servers = servers
    # An HTTP action runs only against a connection frozen with the version
    # and unchanged since; one that was missing when it was submitted is out.
    gone_http = {d.id for d in drift if d.kind == "http_connection"}
    frozen_http = {h["id"] for h in manifest.get("http_connections", [])} - gone_http
    cfg.tools.http_request.actions = [
        a for a in cfg.tools.http_request.actions if a.connection_id in frozen_http
    ]
    sources = [s["id"] for s in manifest.get("knowledge", {}).get("sources", [])]
    if cfg.rag.enabled:
        if sources:
            cfg.rag.source_ids = sorted(sources)
        else:
            # Approved with no knowledge: an empty list would mean "every
            # source", so the search tools are left out instead.
            cfg.rag.enabled = False
    return cfg


async def frozen_for(
    session: AsyncSession, version: AssistantVersion, config: AssistantConfig
) -> tuple[AssistantConfig, Frozen]:
    """`config` (the version's) as a turn may use it, and what it may search.

    A version from before the gate has no manifest: this records one, from
    things as they are at its first turn since, and says so in the audit
    log."""
    if version.manifest is None:
        await record(session, version, config)
        await audit.record(
            session,
            action="assistant.version.manifest_recorded",
            org_id=version.org_id,
            actor_user_id=None,
            target_type="assistant_version",
            target_id=version.id,
            meta={"version": version.version_number, "note": "Recorded at its first turn."},
        )
    manifest = version.manifest
    assert manifest is not None and version.manifest_hash is not None
    drift = await check(session, version.assistant_id, manifest)
    if drift:
        log.warning(
            "version_drift",
            assistant_id=str(version.assistant_id),
            version=version.version_number,
            drift=[d.as_dict() for d in drift],
        )
    documents = frozenset(
        u
        for u in (_uuid(d["id"]) for d in manifest.get("knowledge", {}).get("documents", []))
        if u is not None
    )
    return apply(config, manifest, drift), Frozen(version.manifest_hash, documents, tuple(drift))


# ── kept documents ───────────────────────────────────────────


async def kept_documents(session: AsyncSession, assistant_id: uuid.UUID) -> set[uuid.UUID]:
    """Documents a version of this assistant still needs: the one waiting
    for review, the live one, and the approved one before it (the version a
    rollback returns to). Keeping every approved version's copy would grow
    without end; rolling back further is refused once its documents are
    gone (`check` reports them missing), and the version is resubmitted."""
    live_id = await session.scalar(
        select(Assistant.current_version_id).where(Assistant.id == assistant_id)
    )
    rows = await session.execute(
        select(AssistantVersion.id, AssistantVersion.status, AssistantVersion.manifest)
        .where(
            AssistantVersion.assistant_id == assistant_id,
            AssistantVersion.status.in_(KEEPS),
            AssistantVersion.manifest.is_not(None),
        )
        .order_by(AssistantVersion.version_number.desc())
    )
    out: set[uuid.UUID] = set()
    previous_kept = False
    for vid, status, manifest in rows.all():
        if status is VersionStatus.approved and vid != live_id:
            if previous_kept:
                continue
            previous_kept = True
        for doc in (manifest or {}).get("knowledge", {}).get("documents", []):
            if (u := _uuid(doc["id"])) is not None:
                out.add(u)
    return out


async def versions_using_source(
    session: AsyncSession, assistant_id: uuid.UUID, live_id: uuid.UUID | None, source_id: uuid.UUID
) -> list[int]:
    """The live or submitted versions whose knowledge includes this source:
    deleting it would change what they answer from."""
    rows = await session.execute(
        select(
            AssistantVersion.id,
            AssistantVersion.version_number,
            AssistantVersion.status,
            AssistantVersion.manifest,
        )
        .where(
            AssistantVersion.assistant_id == assistant_id,
            AssistantVersion.status.in_(KEEPS),
            AssistantVersion.manifest.is_not(None),
        )
        .order_by(AssistantVersion.version_number)
    )
    out = []
    for vid, number, status, manifest in rows.all():
        if vid != live_id and status is not VersionStatus.submitted:
            continue
        sources = {s["id"] for s in (manifest or {}).get("knowledge", {}).get("sources", [])}
        if str(source_id) in sources:
            out.append(int(number))
    return out


async def sweep_superseded(session: AsyncSession, assistant_id: uuid.UUID | None = None) -> int:
    """Remove superseded documents no version needs any more (their chunks
    go with them): a version rejected, withdrawn or rolled back no longer
    keeps its copy. Daily, from the worker, and after each reindex."""
    stmt = select(Document.id, Document.assistant_id).where(Document.superseded_at.is_not(None))
    if assistant_id is not None:
        stmt = stmt.where(Document.assistant_id == assistant_id)
    rows = (await session.execute(stmt)).all()
    by_assistant: dict[uuid.UUID, list[uuid.UUID]] = {}
    for doc_id, aid in rows:
        by_assistant.setdefault(aid, []).append(doc_id)
    gone: list[uuid.UUID] = []
    for aid, docs in by_assistant.items():
        keep = await kept_documents(session, aid)
        gone.extend(d for d in docs if d not in keep)
    if gone:
        await session.execute(delete(Document).where(Document.id.in_(gone)))
    return len(gone)


__all__ = [
    "Drift",
    "Frozen",
    "apply",
    "build",
    "check",
    "connection_fingerprint",
    "digest",
    "frozen_for",
    "kept_documents",
    "record",
    "server_fingerprint",
    "sweep_superseded",
    "tool_fingerprint",
    "versions_using_source",
]
