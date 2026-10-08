from __future__ import annotations

import uuid

from fastapi import APIRouter

from app.api.deps import ClientIP, ConversationCtx, CurrentUser, PageQuery, SessionDep
from app.schemas.approval import ApprovalOut, ApprovalResolve
from app.schemas.common import Page
from app.services import approvals as svc

router = APIRouter(tags=["approvals"])


@router.get("/conversations/{conversation_id}/approvals", response_model=Page[ApprovalOut])
async def list_pending(
    ctx: ConversationCtx, session: SessionDep, page: PageQuery
) -> Page[ApprovalOut]:
    """Still-pending approvals for a conversation.

    The UI needs this because the `approval_required` SSE event only reaches
    whoever was watching the stream — reloading the page, or opening it in a
    second tab, must not lose a decision that is genuinely still waiting.
    """
    rows = await svc.list_pending(
        session, ctx.conversation.id, limit=page.limit, cursor=page.cursor
    )
    return Page(
        items=[ApprovalOut.model_validate(r) for r in rows.items], next_cursor=rows.next_cursor
    )


@router.post("/approvals/{approval_id}:resolve", response_model=ApprovalOut)
async def resolve(
    body: ApprovalResolve,
    approval_id: uuid.UUID,
    user: CurrentUser,
    session: SessionDep,
    ip: ClientIP,
) -> ApprovalOut:
    row = await svc.get(session, approval_id)
    # The person in the conversation decides, or an admin reviewing a
    # teammate's pending write. Not any member who can read it.
    await svc.assert_can_decide(session, row, user_id=user.id)
    row = await svc.resolve(session, row, decision=body.decision, user_id=user.id, ip=ip)
    return ApprovalOut.model_validate(row)


__all__ = ["router"]
