from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import (
    ActiveMembership,
    AssistantContext,
    AssistantCtx,
    ClientIP,
    EditableAssistantCtx,
    SessionDep,
)
from app.api.errors import BadRequest
from app.graph.compile import GraphCompileError, compile_graph
from app.graph.nodes import Graph
from app.graph.validate import ValidationResult, validate_graph
from app.schemas.assistant import (
    AssistantCreate,
    AssistantDetail,
    AssistantMetaUpdate,
    AssistantSummary,
    DraftConfigSaveResult,
    DraftGraphSaveResult,
    GraphCompileResult,
    PublishRequest,
    VersionDetail,
    VersionDiff,
    VersionSummary,
)
from app.schemas.assistant_config import AssistantConfig
from app.services import assistants as svc

router = APIRouter(prefix="/assistants", tags=["assistants"])


def _detail(ctx: AssistantContext) -> AssistantDetail:
    a = ctx.assistant
    return AssistantDetail(
        **AssistantSummary.model_validate(a).model_dump(),
        draft_graph=svc.load_graph(a),
        draft_config=svc.load_config(a),
        draft_validation=svc.draft_validation(a),
    )


# ── collection ───────────────────────────────────────────────


@router.get("", response_model=list[AssistantSummary])
async def list_assistants(session: SessionDep, m: ActiveMembership) -> list[AssistantSummary]:
    rows = await svc.list_for_org(session, m.org_id)
    return [AssistantSummary.model_validate(a) for a in rows]


@router.post("", response_model=AssistantDetail, status_code=status.HTTP_201_CREATED)
async def create_assistant(
    body: AssistantCreate, session: SessionDep, m: ActiveMembership, ip: ClientIP
) -> AssistantDetail:
    a = await svc.create(
        session,
        org_id=m.org_id,
        user_id=m.user_id,
        name=body.name,
        description=body.description,
        ip=ip,
    )
    return _detail(AssistantContext(assistant=a, membership=m))


# ── single ───────────────────────────────────────────────────


@router.get("/{assistant_id}", response_model=AssistantDetail)
async def get_assistant(ctx: AssistantCtx) -> AssistantDetail:
    return _detail(ctx)


@router.patch("/{assistant_id}", response_model=AssistantDetail)
async def update_assistant(
    body: AssistantMetaUpdate, ctx: EditableAssistantCtx, session: SessionDep, ip: ClientIP
) -> AssistantDetail:
    await svc.update_meta(
        session,
        ctx.assistant,
        name=body.name,
        description=body.description,
        status=body.status,
        actor_id=ctx.membership.user_id,
        ip=ip,
    )
    return _detail(ctx)


# ── draft graph / config ─────────────────────────────────────


@router.get("/{assistant_id}/draft-graph", response_model=Graph)
async def get_draft_graph(ctx: AssistantCtx) -> Graph:
    return svc.load_graph(ctx.assistant)


@router.put("/{assistant_id}/draft-graph", response_model=DraftGraphSaveResult)
async def put_draft_graph(
    body: Graph, ctx: EditableAssistantCtx, session: SessionDep
) -> DraftGraphSaveResult:
    return await svc.save_draft_graph(session, ctx.assistant, body)


@router.put("/{assistant_id}/draft-config", response_model=DraftConfigSaveResult)
async def put_draft_config(
    body: AssistantConfig, ctx: EditableAssistantCtx, session: SessionDep
) -> DraftConfigSaveResult:
    return await svc.save_draft_config(session, ctx.assistant, body)


# ── dry-run helpers (no persistence) ─────────────────────────


@router.post("/{assistant_id}/graph:validate", response_model=ValidationResult)
async def dry_run_validate(body: Graph, ctx: AssistantCtx) -> ValidationResult:
    return validate_graph(body)


@router.post("/{assistant_id}/graph:compile", response_model=GraphCompileResult)
async def dry_run_compile(body: Graph, ctx: AssistantCtx) -> GraphCompileResult:
    try:
        return GraphCompileResult(config=compile_graph(body))
    except GraphCompileError as exc:
        raise BadRequest(
            "Graph does not compile", code="graph_invalid", details={"errors": exc.messages}
        ) from exc


# ── versions ─────────────────────────────────────────────────


@router.post(
    "/{assistant_id}/versions", response_model=VersionDetail, status_code=status.HTTP_201_CREATED
)
async def publish_version(
    body: PublishRequest, ctx: EditableAssistantCtx, session: SessionDep, ip: ClientIP
) -> VersionDetail:
    version = await svc.publish(
        session, ctx.assistant, actor_id=ctx.membership.user_id, note=body.note, ip=ip
    )
    return VersionDetail.model_validate(version)


@router.get("/{assistant_id}/versions", response_model=list[VersionSummary])
async def list_versions(ctx: AssistantCtx, session: SessionDep) -> list[VersionSummary]:
    rows = await svc.list_versions(session, ctx.assistant.id)
    return [VersionSummary.model_validate(v) for v in rows]


@router.get("/{assistant_id}/versions/diff", response_model=VersionDiff)
async def diff_versions(
    ctx: AssistantCtx,
    session: SessionDep,
    a: Annotated[int, Query(ge=1)],
    b: Annotated[int, Query(ge=1)],
) -> VersionDiff:
    return await svc.diff_versions(session, ctx.assistant.id, a, b)


@router.get("/{assistant_id}/versions/{number}", response_model=VersionDetail)
async def get_version(ctx: AssistantCtx, session: SessionDep, number: int) -> VersionDetail:
    version = await svc.get_version(session, ctx.assistant.id, number)
    return VersionDetail.model_validate(version)
