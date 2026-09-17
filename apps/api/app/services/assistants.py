"""Assistant + version operations: drafts, compile/validate, publish, diff."""

from __future__ import annotations

import secrets
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import BadRequest, NotFound
from app.graph.compile import compile_graph
from app.graph.nodes import Graph
from app.graph.project import project_config
from app.graph.validate import ValidationResult, validate_graph
from app.models.assistant import Assistant, AssistantStatus, AssistantVersion
from app.schemas.assistant import (
    DraftConfigSaveResult,
    DraftGraphSaveResult,
    VersionDiff,
)
from app.schemas.assistant_config import AssistantConfig, default_config
from app.services import audit
from app.services.diff import diff_graphs, diff_values
from app.services.slug import slugify

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
    return validate_graph(load_graph(assistant))


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


async def list_for_org(session: AsyncSession, org_id: uuid.UUID) -> list[Assistant]:
    rows = await session.scalars(
        select(Assistant).where(Assistant.org_id == org_id).order_by(Assistant.updated_at.desc())
    )
    return list(rows)


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
    return assistant


# ── drafts ───────────────────────────────────────────────────


async def save_draft_graph(
    session: AsyncSession, assistant: Assistant, graph: Graph
) -> DraftGraphSaveResult:
    assistant.draft_graph = _dump(graph)
    validation = validate_graph(graph)
    config: AssistantConfig | None = None
    if validation.ok:
        config = compile_graph(graph)
        assistant.draft_config = _dump(config)
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
    await session.commit()
    return DraftConfigSaveResult(config=config, graph=graph)


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
    validation = validate_graph(graph)
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


async def list_versions(session: AsyncSession, assistant_id: uuid.UUID) -> list[AssistantVersion]:
    rows = await session.scalars(
        select(AssistantVersion)
        .where(AssistantVersion.assistant_id == assistant_id)
        .order_by(AssistantVersion.version_number.desc())
    )
    return list(rows)


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


__all__ = [
    "create",
    "diff_versions",
    "draft_validation",
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
