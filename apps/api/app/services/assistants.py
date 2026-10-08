"""Assistant + version operations: drafts, compile/validate, publish, diff."""

from __future__ import annotations

import secrets
import uuid

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import cli_files, session_store
from app.api.errors import BadRequest, NotFound
from app.db.pagination import PageResult, keyset_page
from app.graph.compile import compile_graph
from app.graph.nodes import Graph
from app.graph.project import project_config
from app.graph.validate import ValidationResult, validate_graph
from app.logging import get_logger
from app.models.assistant import Assistant, AssistantStatus, AssistantVersion
from app.models.conversation import Conversation
from app.models.integration import DbConnection, McpServer
from app.models.rag import DataSource
from app.models.secret import Secret
from app.schemas.assistant import (
    DraftConfigSaveResult,
    DraftGraphSaveResult,
    VersionDiff,
)
from app.schemas.assistant_config import AssistantConfig, default_config
from app.services import audit
from app.services.diff import diff_graphs, diff_values
from app.services.graph_refs import reference_issues
from app.services.slug import slugify
from app.storage.s3 import delete_object

log = get_logger(__name__)

# ── helpers ──────────────────────────────────────────────────


def _dump(model: AssistantConfig | Graph) -> dict:
    return model.model_dump(mode="json")


async def _unique_slug(session: AsyncSession, org_id: uuid.UUID, name: str) -> str:
    base = slugify(name)[:100]
    candidate = base
    for _ in range(6):
        clash = await session.scalar(
            select(Assistant.id).where(Assistant.org_id == org_id, Assistant.slug == candidate)
        )
        if clash is None:
            return candidate
        candidate = f"{base}-{secrets.token_hex(3)}"
    return f"{base}-{secrets.token_hex(6)}"


def load_config(assistant: Assistant) -> AssistantConfig:
    return AssistantConfig.model_validate(assistant.draft_config)


def load_graph(assistant: Assistant) -> Graph:
    return Graph.model_validate(assistant.draft_graph)


def draft_validation(assistant: Assistant) -> ValidationResult:
    """Structural checks only (pure). See `full_validation` for references."""
    return validate_graph(load_graph(assistant))


async def full_validation(
    session: AsyncSession, assistant: Assistant, graph: Graph | None = None
) -> ValidationResult:
    """Structure *and* references. The structural validator is pure and cannot
    see whether a node's connection or data source actually exists, belongs
    to this assistant, or allows what the node asks of it; this adds that."""
    graph = graph if graph is not None else load_graph(assistant)
    structural = validate_graph(graph)
    references = await reference_issues(session, assistant.id, graph)
    return ValidationResult(
        errors=[*structural.errors, *references], warnings=list(structural.warnings)
    )


# ── CRUD ─────────────────────────────────────────────────────


async def create(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    name: str,
    description: str,
    ip: str | None = None,
) -> Assistant:
    config = default_config()
    graph = project_config(config)
    assistant = Assistant(
        org_id=org_id,
        name=name.strip(),
        slug=await _unique_slug(session, org_id, name),
        description=description.strip(),
        created_by=user_id,
        status=AssistantStatus.draft,
        draft_graph=_dump(graph),
        draft_config=_dump(config),
    )
    session.add(assistant)
    await session.flush()
    await audit.record(
        session,
        action="assistant.create",
        org_id=org_id,
        actor_user_id=user_id,
        target_type="assistant",
        target_id=assistant.id,
        ip=ip,
    )
    await session.commit()
    return assistant


async def get(session: AsyncSession, *, org_id: uuid.UUID, assistant_id: uuid.UUID) -> Assistant:
    assistant = await session.scalar(
        select(Assistant).where(Assistant.id == assistant_id, Assistant.org_id == org_id)
    )
    if assistant is None:
        raise NotFound("Assistant not found")
    return assistant


async def list_for_org(
    session: AsyncSession, org_id: uuid.UUID, *, limit: int = 50, cursor: str | None = None
) -> PageResult[Assistant]:
    # Most recently edited first. `updated_at` is mutable, so an assistant
    # edited while someone pages through the list jumps to page 1 and can be
    # missed on page 2. Accepted: it is back on top at the next refresh, and
    # "recently worked on first" is what this list is for.
    return await keyset_page(
        session,
        select(Assistant).where(Assistant.org_id == org_id),
        [Assistant.updated_at, Assistant.id],
        limit=limit,
        cursor=cursor,
    )


async def update_meta(
    session: AsyncSession,
    assistant: Assistant,
    *,
    name: str | None,
    description: str | None,
    status: AssistantStatus | None,
    actor_id: uuid.UUID,
    ip: str | None = None,
) -> Assistant:
    if name is not None:
        assistant.name = name.strip()
    if description is not None:
        assistant.description = description.strip()
    if status is not None:
        assistant.status = status
    await session.flush()
    await audit.record(
        session,
        action="assistant.update",
        org_id=assistant.org_id,
        actor_user_id=actor_id,
        target_type="assistant",
        target_id=assistant.id,
        ip=ip,
    )
    await session.commit()
    # `updated_at` is set by the database on UPDATE, so after the commit it is
    # expired; reading it while building the response was a lazy load outside
    # the async context (MissingGreenlet, a 500 on every rename). Postgres
    # happens to return it with the UPDATE, which is why the dev stack never
    # showed it; nothing tested a rename before the role matrix did.
    await session.refresh(assistant)
    return assistant


# ── drafts ───────────────────────────────────────────────────


async def save_draft_graph(
    session: AsyncSession, assistant: Assistant, graph: Graph
) -> DraftGraphSaveResult:
    assistant.draft_graph = _dump(graph)
    config: AssistantConfig | None = None
    # Compile on *structure* alone: a bad reference must not freeze the draft
    # config (and with it the panels) — it only has to block publishing.
    if validate_graph(graph).ok:
        config = compile_graph(graph)
        assistant.draft_config = _dump(config)
    validation = await full_validation(session, assistant, graph)
    await session.flush()
    await session.commit()
    return DraftGraphSaveResult(graph=graph, validation=validation, config=config)


async def save_draft_config(
    session: AsyncSession, assistant: Assistant, config: AssistantConfig
) -> DraftConfigSaveResult:
    existing = None
    try:
        existing = load_graph(assistant)
    except Exception:
        existing = None
    graph = project_config(config, existing_graph=existing)
    assistant.draft_config = _dump(config)
    assistant.draft_graph = _dump(graph)
    await session.flush()
    validation = await full_validation(session, assistant, graph)
    await session.commit()
    return DraftConfigSaveResult(config=config, graph=graph, validation=validation)


# ── publish / versions ───────────────────────────────────────


async def publish(
    session: AsyncSession,
    assistant: Assistant,
    *,
    actor_id: uuid.UUID,
    note: str,
    ip: str | None = None,
) -> AssistantVersion:
    graph = load_graph(assistant)
    validation = await full_validation(session, assistant, graph)
    if not validation.ok:
        raise BadRequest(
            "The draft graph has validation errors; fix them before publishing",
            code="graph_invalid",
            details={"errors": [e.model_dump() for e in validation.errors]},
        )
    config = compile_graph(graph)

    highest = await session.scalar(
        select(func.max(AssistantVersion.version_number)).where(
            AssistantVersion.assistant_id == assistant.id
        )
    )
    version = AssistantVersion(
        assistant_id=assistant.id,
        org_id=assistant.org_id,
        version_number=(highest or 0) + 1,
        graph=_dump(graph),
        config=_dump(config),
        created_by=actor_id,
        note=note.strip(),
    )
    session.add(version)
    await session.flush()

    assistant.current_version_id = version.id
    assistant.status = AssistantStatus.published
    assistant.draft_config = _dump(config)  # keep the draft canonical
    await audit.record(
        session,
        action="assistant.publish",
        org_id=assistant.org_id,
        actor_user_id=actor_id,
        target_type="assistant_version",
        target_id=version.id,
        meta={"version": version.version_number},
        ip=ip,
    )
    await session.commit()
    return version


async def list_versions(
    session: AsyncSession, assistant_id: uuid.UUID, *, limit: int = 50, cursor: str | None = None
) -> PageResult[AssistantVersion]:
    # version_number is unique per assistant, so it is a total order alone.
    return await keyset_page(
        session,
        select(AssistantVersion).where(AssistantVersion.assistant_id == assistant_id),
        [AssistantVersion.version_number],
        limit=limit,
        cursor=cursor,
    )


async def get_version(
    session: AsyncSession, assistant_id: uuid.UUID, number: int
) -> AssistantVersion:
    version = await session.scalar(
        select(AssistantVersion).where(
            AssistantVersion.assistant_id == assistant_id,
            AssistantVersion.version_number == number,
        )
    )
    if version is None:
        raise NotFound(f"Version {number} not found")
    return version


async def diff_versions(
    session: AsyncSession, assistant_id: uuid.UUID, a: int, b: int
) -> VersionDiff:
    va = await get_version(session, assistant_id, a)
    vb = await get_version(session, assistant_id, b)
    return VersionDiff(
        from_version=a,
        to_version=b,
        config_diff=diff_values(va.config, vb.config),
        graph_diff=diff_graphs(va.graph, vb.graph),
    )


async def delete(
    session: AsyncSession,
    assistant: Assistant,
    *,
    actor_user_id: uuid.UUID,
    ip: str | None = None,
) -> None:
    """Delete an assistant and everything it owns (task 1.2 / plan §10).

    The database cascade takes versions, conversations (with their messages,
    runs and approvals), data sources, documents, chunks and connections.
    Three things it cannot reach are handled here:

    - **sealed credentials** — `db_connections -> secrets` and
      `mcp_servers -> secrets` are SET NULL the other way round, so the
      cascade alone would orphan every ciphertext. Deleted in the same
      commit.
    - **uploaded files, session cache, scratch dirs** — outside Postgres.
      Removed *after* the commit, best-effort: a storage outage then leaves
      an orphaned file (logged), never rows pointing at deleted files.

    Usage history deliberately survives, with the assistant reference nulled.
    """
    assistant_id = assistant.id
    connections = (
        await session.scalars(select(DbConnection).where(DbConnection.assistant_id == assistant_id))
    ).all()
    servers = (
        await session.scalars(select(McpServer).where(McpServer.assistant_id == assistant_id))
    ).all()
    secret_ids = [
        ref for c in connections for ref in (c.secret_ref, c.uri_secret_ref) if ref is not None
    ] + [
        ref for m in servers for ref in (m.headers_secret_ref, m.env_secret_ref) if ref is not None
    ]
    object_keys = [
        key
        for key in (
            await session.scalars(
                select(DataSource.object_key).where(DataSource.assistant_id == assistant_id)
            )
        ).all()
        if key
    ]
    conversation_ids = list(
        (
            await session.scalars(
                select(Conversation.id).where(Conversation.assistant_id == assistant_id)
            )
        ).all()
    )

    await audit.record(
        session,
        action="assistant.delete",
        org_id=assistant.org_id,
        actor_user_id=actor_user_id,
        target_type="assistant",
        target_id=assistant_id,
        meta={
            "name": assistant.name,
            "connections": len(connections),
            "files": len(object_keys),
            "conversations": len(conversation_ids),
        },
        ip=ip,
    )
    await session.delete(assistant)
    await session.flush()
    if secret_ids:
        await session.execute(sa_delete(Secret).where(Secret.id.in_(secret_ids)))
    await session.commit()

    for key in object_keys:
        try:
            await delete_object(key)
        except Exception as exc:
            log.warning("assistant_delete_object_failed", key=key, error=str(exc)[:200])
    for conversation_id in conversation_ids:
        await session_store.delete(conversation_id)
        cli_files.discard(conversation_id)


__all__ = [
    "create",
    "delete",
    "diff_versions",
    "draft_validation",
    "full_validation",
    "get",
    "get_version",
    "list_for_org",
    "list_versions",
    "load_config",
    "load_graph",
    "publish",
    "save_draft_config",
    "save_draft_graph",
    "update_meta",
]
