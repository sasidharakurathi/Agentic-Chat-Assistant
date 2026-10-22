"""Hand-offs to a person (Phase 9.5): the person who started a
conversation hands it off; whoever may read it sees its hand-offs."""

from __future__ import annotations

from fastapi import APIRouter, status

from app.api.deps import (
    ChatConversationStarterCtx,
    ChatReadableConversationCtx,
    ClientIP,
    CurrentUser,
    SessionDep,
)
from app.schemas.handoff import HandoffCreate, HandoffList, HandoffOut
from app.services import handoffs as svc

router = APIRouter(tags=["handoffs"])


@router.post(
    "/conversations/{conversation_id}/handoff",
    response_model=HandoffOut,
    status_code=status.HTTP_201_CREATED,
)
async def hand_off(
    body: HandoffCreate,
    ctx: ChatConversationStarterCtx,
    user: CurrentUser,
    session: SessionDep,
    ip: ClientIP,
) -> HandoffOut:
    """Get a person: the assistant's contact is emailed a link to this
    conversation, or a ticket is opened, as its settings say. The answer
    says whether it went through (`status`)."""
    row = await svc.request(session, ctx.conversation, user=user, note=body.note, ip=ip)
    return HandoffOut.model_validate(row)


@router.get("/conversations/{conversation_id}/handoffs", response_model=HandoffList)
async def list_handoffs(ctx: ChatReadableConversationCtx, session: SessionDep) -> HandoffList:
    """The conversation's hand-offs with their summaries, and whether it
    can be handed off now and to whom."""
    return await svc.list_for(session, ctx.conversation)


__all__ = ["router"]
