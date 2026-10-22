"""The needs-review queue (Phase 10.3): answers people complained about,
for the assistant's builders to turn into test cases or dismiss. Never who
asked; see `services/review_queue.py`."""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Path, Query

from app.api.deps import ClientIP, EditableAssistantCtx, SessionDep
from app.models.enums import MemberRole
from app.models.review import ReviewState
from app.schemas.review import ReviewDismissIn, ReviewItemOut, ReviewToCaseIn
from app.services import review_queue

router = APIRouter(prefix="/assistants", tags=["review queue"])


def _out(item: review_queue.Item) -> ReviewItemOut:
    return ReviewItemOut.model_validate({**vars(item), "state": item.state.value})


async def _one(session: SessionDep, ctx: EditableAssistantCtx, item_id: uuid.UUID) -> ReviewItemOut:
    admin = ctx.membership.role.satisfies(MemberRole.admin)
    return _out(await review_queue.one(session, ctx.assistant, item_id, admin=admin))


@router.get("/{assistant_id}/review-queue", response_model=list[ReviewItemOut])
async def list_review_queue(
    ctx: EditableAssistantCtx,
    session: SessionDep,
    state: Annotated[Literal["open", "turned_into_case", "dismissed"], Query()] = "open",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[ReviewItemOut]:
    """Answers their askers said needed work: a thumbs down, or "Did this
    solve it?" said no (a failed or declined turn is noted on those, never
    listed by itself). Open ones newest first; decided
    ones by when they were decided. With the question and answer, never
    who asked; an admin also gets the conversation, to open with a
    recorded reason. Live conversations only."""
    items = await review_queue.listing(
        session,
        ctx.assistant,
        state=ReviewState(state),
        admin=ctx.membership.role.satisfies(MemberRole.admin),
        limit=limit,
    )
    return [_out(i) for i in items]


@router.post("/{assistant_id}/review-queue/{item_id}:to-case", response_model=ReviewItemOut)
async def review_item_to_case(
    item_id: Annotated[uuid.UUID, Path()],
    body: ReviewToCaseIn,
    ctx: EditableAssistantCtx,
    session: SessionDep,
    ip: ClientIP,
) -> ReviewItemOut:
    """Turn it into a test case in one of this assistant's suites: the
    asker's question (or `input`) and what a good answer looks like. When
    the case passes on the live version, the asker is told, once."""
    await review_queue.to_case(
        session,
        ctx.assistant,
        item_id,
        suite_id=body.suite_id,
        question=body.input,
        expected=body.expected,
        actor_id=ctx.membership.user_id,
        ip=ip,
    )
    return await _one(session, ctx, item_id)


@router.post("/{assistant_id}/review-queue/{item_id}:dismiss", response_model=ReviewItemOut)
async def review_item_dismiss(
    item_id: Annotated[uuid.UUID, Path()],
    body: ReviewDismissIn,
    ctx: EditableAssistantCtx,
    session: SessionDep,
    ip: ClientIP,
) -> ReviewItemOut:
    """Nothing to fix here, and why. Recorded with who decided."""
    await review_queue.dismiss(
        session,
        ctx.assistant,
        item_id,
        reason=body.reason,
        actor_id=ctx.membership.user_id,
        ip=ip,
    )
    return await _one(session, ctx, item_id)


__all__ = ["router"]
