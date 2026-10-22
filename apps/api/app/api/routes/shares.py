"""Read-only conversation links (Phase 9.4): the person who started a
conversation shares and revokes; a member opens, if it opens for them."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from app.api.deps import (
    ChatConversationStarterCtx,
    ChatReadableConversationCtx,
    ClientIP,
    CurrentUser,
    SessionDep,
)
from app.schemas.share import ShareCreated, SharedConversationOut, ShareOut
from app.services import shares as svc

router = APIRouter(tags=["shares"])


@router.post(
    "/conversations/{conversation_id}/shares",
    response_model=ShareCreated,
    status_code=status.HTTP_201_CREATED,
)
async def create_share(
    ctx: ChatConversationStarterCtx, session: SessionDep, ip: ClientIP
) -> ShareCreated:
    return await svc.create(session, ctx.conversation, user_id=ctx.membership.user_id, ip=ip)


@router.get("/conversations/{conversation_id}/shares", response_model=list[ShareOut])
async def list_shares(ctx: ChatReadableConversationCtx, session: SessionDep) -> list[ShareOut]:
    return await svc.list_for(session, ctx.conversation)


@router.delete("/conversations/{conversation_id}/shares/{share_id}", response_model=ShareOut)
async def revoke_share(
    share_id: uuid.UUID, ctx: ChatConversationStarterCtx, session: SessionDep, ip: ClientIP
) -> ShareOut:
    return await svc.revoke(
        session, ctx.conversation, share_id, user_id=ctx.membership.user_id, ip=ip
    )


@router.get("/shared/{token}", response_model=SharedConversationOut)
async def open_share(
    token: str, user: CurrentUser, session: SessionDep, ip: ClientIP
) -> SharedConversationOut:
    """The shared copy. Signed in, a member of the conversation's
    organisation, and allowed its assistant and the knowledge it cites."""
    return await svc.open_link(session, token[:200], user=user, ip=ip)


__all__ = ["router"]
