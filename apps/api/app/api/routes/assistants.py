from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import samples
from app.api.deps import (
    ActiveMembership,
    AdminAssistantCtx,
    AssistantContext,
    AssistantCtx,
    ClientIP,
    EditableAssistantCtx,
    OwnerAssistantCtx,
    PageQuery,
    SessionDep,
)
from app.api.errors import BadRequest, NotFound
from app.graph.compile import GraphCompileError, compile_graph
from app.graph.nodes import Graph
from app.graph.validate import ValidationResult
from app.models.enums import MemberRole
from app.schemas.approval_evidence import VersionEvidenceOut
from app.schemas.assistant import (
    ApproveRequest,
    ArchiveRequest,
    AssistantCreate,
    AssistantDetail,
    AssistantFromSample,
    AssistantMetaUpdate,
    AssistantSummary,
    DraftConfigSaveResult,
    DraftGraphSaveResult,
    EditorAdd,
    EditorOut,
    GraphCompileResult,
    PublishRequest,
    RejectRequest,
    VersionDetail,
    VersionDiff,
    VersionDrift,
    VersionSummary,
)
from app.schemas.assistant_config import AssistantConfig
from app.schemas.common import Message, Page
from app.schemas.feedback import FeedbackCommentOut, FeedbackSummaryOut, VersionCountOut
from app.schemas.listing import Listing, OfficialIn
from app.services import approval_evidence as evidence_svc
from app.services import assistants as svc
from app.services import audit, editors
from app.services import feedback as feedback_service
from app.services import listing as listing_service
from app.services import samples as samples_svc

router = APIRouter(prefix="/assistants", tags=["assistants"])


async def _detail(ctx: AssistantContext, session: AsyncSession) -> AssistantDetail:
    a = ctx.assistant
    return AssistantDetail(
        **AssistantSummary.model_validate(a).model_dump(),
        draft_graph=svc.load_graph(a),
        draft_config=svc.load_config(a),
        # With references, so an error survives a page reload rather than
        # only appearing in the save response.
        draft_validation=await svc.full_validation(session, a),
        can_edit=ctx.can_edit,
    )


# ── collection ───────────────────────────────────────────────


@router.get("", response_model=Page[AssistantSummary])
async def list_assistants(
    session: SessionDep, m: ActiveMembership, page: PageQuery
) -> Page[AssistantSummary]:
    rows = await svc.list_for_org(session, m.org_id, limit=page.limit, cursor=page.cursor)
    return Page(
        items=[AssistantSummary.model_validate(a) for a in rows.items],
        next_cursor=rows.next_cursor,
    )


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
    return await _detail(AssistantContext(assistant=a, membership=m, can_edit=True), session)


@router.post(":from-sample", response_model=AssistantDetail, status_code=status.HTTP_201_CREATED)
async def create_from_sample(
    body: AssistantFromSample, session: SessionDep, m: ActiveMembership, ip: ClientIP
) -> AssistantDetail:
    """A new draft from a shipped sample (`GET /meta/samples`): its graph,
    its documents queued for indexing, and its eval suite."""
    sample = samples.get(body.sample_id)
    if sample is None:
        raise NotFound("No such sample", code="sample_not_found")
    a = await samples_svc.create_from(
        session, sample, org_id=m.org_id, user_id=m.user_id, name=body.name, ip=ip
    )
    return await _detail(AssistantContext(assistant=a, membership=m, can_edit=True), session)


# ── single ───────────────────────────────────────────────────


@router.get("/{assistant_id}", response_model=AssistantDetail)
async def get_assistant(ctx: AssistantCtx, session: SessionDep) -> AssistantDetail:
    return await _detail(ctx, session)


@router.patch("/{assistant_id}", response_model=AssistantDetail)
async def update_assistant(
    body: AssistantMetaUpdate, ctx: EditableAssistantCtx, session: SessionDep, ip: ClientIP
) -> AssistantDetail:
    await svc.update_meta(
        session,
        ctx.assistant,
        name=body.name,
        description=body.description,
        actor_id=ctx.membership.user_id,
        ip=ip,
    )
    return await _detail(ctx, session)


# ── draft graph / config ─────────────────────────────────────


@router.delete("/{assistant_id}", response_model=Message)
async def delete_assistant(ctx: EditableAssistantCtx, session: SessionDep, ip: ClientIP) -> Message:
    """Delete an assistant and everything it owns. Same rule as editing it:
    its creator, or an admin."""
    await svc.delete(session, ctx.assistant, actor_user_id=ctx.membership.user_id, ip=ip)
    return Message(message="deleted")


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
async def dry_run_validate(body: Graph, ctx: AssistantCtx, session: SessionDep) -> ValidationResult:
    return await svc.full_validation(session, ctx.assistant, body)


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
async def submit_version(
    body: PublishRequest, ctx: EditableAssistantCtx, session: SessionDep, ip: ClientIP
) -> VersionDetail:
    """Submit the draft for review (Phase 7b.1). It answers in Chat only once
    an admin approves it."""
    version = await svc.submit(
        session, ctx.assistant, actor_id=ctx.membership.user_id, note=body.note, ip=ip
    )
    return VersionDetail.model_validate(version)


@router.get("/{assistant_id}/feedback", response_model=FeedbackSummaryOut)
async def assistant_feedback(
    ctx: EditableAssistantCtx,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> FeedbackSummaryOut:
    """What people said about its answers (Phase 8.8): thumbs by version,
    "Did this solve it?" with how many could have said, and the latest
    comments with their question and answer. Never who asked; an admin also
    gets the conversation, to open with a recorded reason."""
    admin = ctx.membership.role.satisfies(MemberRole.admin)
    out = await feedback_service.summary(session, ctx.assistant, admin=admin, limit=limit)
    return FeedbackSummaryOut(
        versions=[VersionCountOut(**vars(v)) for v in out.versions],
        solved_yes=out.solved_yes,
        solved_no=out.solved_no,
        conversations=out.conversations,
        comments=[FeedbackCommentOut(**vars(c)) for c in out.comments],
    )


@router.get("/{assistant_id}/listing", response_model=Listing)
async def get_listing(ctx: AssistantCtx) -> Listing:
    """The draft listing (Phase 8.5): category, starters, audience. What Chat
    shows is the live version's."""
    return listing_service.draft_of(ctx.assistant)


@router.put("/{assistant_id}/listing", response_model=Listing)
async def put_listing(body: Listing, ctx: EditableAssistantCtx, session: SessionDep) -> Listing:
    """Save the draft listing. It reaches Chat with the next approved
    version, reviewed with it."""
    return await listing_service.save_draft(session, ctx.assistant, body)


@router.post("/{assistant_id}:official", response_model=AssistantSummary)
async def set_official(
    body: OfficialIn, ctx: AdminAssistantCtx, session: SessionDep, ip: ClientIP
) -> AssistantSummary:
    """Mark it Official, or not (Phase 8.5): admins vouch for it, and the
    catalog shows it first."""
    a = ctx.assistant
    was = a.official_at is not None
    if body.official != was:
        a.official_at = datetime.now(UTC) if body.official else None
        a.official_by = ctx.membership.user_id if body.official else None
        await audit.record(
            session,
            action="assistant.official",
            org_id=a.org_id,
            actor_user_id=ctx.membership.user_id,
            target_type="assistant",
            target_id=a.id,
            meta={"official": body.official},
            ip=ip,
        )
        await session.commit()
        await session.refresh(a)
    return AssistantSummary.model_validate(a)


@router.post("/{assistant_id}/versions/{number}/approve", response_model=VersionDetail)
async def approve_version(
    number: int, body: ApproveRequest, ctx: AdminAssistantCtx, session: SessionDep, ip: ClientIP
) -> VersionDetail:
    version = await svc.approve(
        session,
        ctx.assistant,
        number,
        actor_id=ctx.membership.user_id,
        actor_role=ctx.membership.role,
        note=body.note,
        ip=ip,
    )
    return VersionDetail.model_validate(version)


@router.post("/{assistant_id}/versions/{number}/reject", response_model=VersionDetail)
async def reject_version(
    number: int, body: RejectRequest, ctx: AdminAssistantCtx, session: SessionDep, ip: ClientIP
) -> VersionDetail:
    version = await svc.reject(
        session, ctx.assistant, number, actor_id=ctx.membership.user_id, note=body.note, ip=ip
    )
    return VersionDetail.model_validate(version)


@router.post("/{assistant_id}/versions/{number}/withdraw", response_model=VersionDetail)
async def withdraw_version(
    number: int, ctx: EditableAssistantCtx, session: SessionDep, ip: ClientIP
) -> VersionDetail:
    version = await svc.withdraw(
        session, ctx.assistant, number, actor_id=ctx.membership.user_id, ip=ip
    )
    return VersionDetail.model_validate(version)


@router.post("/{assistant_id}/versions/{number}/make-live", response_model=VersionDetail)
async def make_version_live(
    number: int, ctx: AdminAssistantCtx, session: SessionDep, ip: ClientIP
) -> VersionDetail:
    """Roll back to an earlier approved version."""
    version = await svc.make_live(
        session, ctx.assistant, number, actor_id=ctx.membership.user_id, ip=ip
    )
    return VersionDetail.model_validate(version)


@router.post("/{assistant_id}/archive", response_model=AssistantDetail)
async def archive_assistant(
    body: ArchiveRequest, ctx: AdminAssistantCtx, session: SessionDep, ip: ClientIP
) -> AssistantDetail:
    """Pause: nothing answers until an admin reopens it."""
    await svc.archive(
        session, ctx.assistant, actor_id=ctx.membership.user_id, reason=body.reason, ip=ip
    )
    return await _detail(ctx, session)


@router.post("/{assistant_id}/unarchive", response_model=AssistantDetail)
async def unarchive_assistant(
    ctx: AdminAssistantCtx, session: SessionDep, ip: ClientIP
) -> AssistantDetail:
    await svc.unarchive(session, ctx.assistant, actor_id=ctx.membership.user_id, ip=ip)
    return await _detail(ctx, session)


@router.get("/{assistant_id}/versions", response_model=Page[VersionSummary])
async def list_versions(
    ctx: AssistantCtx, session: SessionDep, page: PageQuery
) -> Page[VersionSummary]:
    rows = await svc.list_versions(session, ctx.assistant.id, limit=page.limit, cursor=page.cursor)
    return Page(
        items=[VersionSummary.model_validate(v) for v in rows.items],
        next_cursor=rows.next_cursor,
    )


@router.get("/{assistant_id}/versions/diff", response_model=VersionDiff)
async def diff_versions(
    ctx: AssistantCtx,
    session: SessionDep,
    a: Annotated[int, Query(ge=1)],
    b: Annotated[int, Query(ge=1)],
) -> VersionDiff:
    return await svc.diff_versions(session, ctx.assistant.id, a, b)


# ── co-editors (Phase 7b.4) ──────────────────────────────────


@router.get("/{assistant_id}/editors", response_model=list[EditorOut])
async def list_editors(ctx: AssistantCtx, session: SessionDep) -> list[EditorOut]:
    """Who besides its creator may edit it."""
    return [EditorOut.model_validate(e) for e in await editors.list_for(session, ctx.assistant)]


@router.post(
    "/{assistant_id}/editors", response_model=list[EditorOut], status_code=status.HTTP_201_CREATED
)
async def add_editor(
    body: EditorAdd, ctx: OwnerAssistantCtx, session: SessionDep, ip: ClientIP
) -> list[EditorOut]:
    """Its creator or an admin names a co-editor: an active member of the
    organisation."""
    await editors.add(
        session, ctx.assistant, user_id=body.user_id, actor_id=ctx.membership.user_id, ip=ip
    )
    return [EditorOut.model_validate(e) for e in await editors.list_for(session, ctx.assistant)]


@router.delete("/{assistant_id}/editors/{user_id}", response_model=Message)
async def remove_editor(
    user_id: uuid.UUID, ctx: OwnerAssistantCtx, session: SessionDep, ip: ClientIP
) -> Message:
    await editors.remove(
        session, ctx.assistant, user_id=user_id, actor_id=ctx.membership.user_id, ip=ip
    )
    return Message(message="removed")


@router.get("/{assistant_id}/versions/{number}/drift", response_model=VersionDrift)
async def version_drift(ctx: AssistantCtx, session: SessionDep, number: int) -> VersionDrift:
    """What has changed since this version was submitted (Phase 7b.1)."""
    return await svc.drift(session, ctx.assistant, number)


@router.get("/{assistant_id}/versions/{number}/evidence", response_model=VersionEvidenceOut)
async def version_evidence(
    ctx: AdminAssistantCtx,
    session: SessionDep,
    number: int,
    per_month: Annotated[int, Query(ge=1, le=10_000_000)] = evidence_svc.DEFAULT_PER_MONTH,
) -> VersionEvidenceOut:
    """What to approve on (Phase 10.4): the eval scorecard, the "no
    unapproved write" safety line and a cost forecast for `per_month`
    conversations. For the people who review: read-only, so not audited."""
    return await evidence_svc.evidence(session, ctx.assistant, number, per_month=per_month)


@router.get("/{assistant_id}/versions/{number}", response_model=VersionDetail)
async def get_version(ctx: AssistantCtx, session: SessionDep, number: int) -> VersionDetail:
    version = await svc.get_version(session, ctx.assistant.id, number)
    return VersionDetail.model_validate(version)
