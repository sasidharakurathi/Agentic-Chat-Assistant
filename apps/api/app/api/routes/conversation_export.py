"""Exporting a conversation (Phase 10.5): a file to keep, for whoever may
read it. See `services/conversation_export.py` for what goes in it."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query, Response

from app.api.deps import ChatReadableConversationCtx, ClientIP, SessionDep
from app.services import audit, conversation_export

router = APIRouter(tags=["conversations"])


@router.get(
    "/conversations/{conversation_id}/export",
    response_class=Response,
    responses={
        200: {
            "content": {"text/markdown": {}, "application/json": {}},
            "description": "The conversation as a file to save.",
        }
    },
)
async def export_conversation(
    ctx: ChatReadableConversationCtx,
    session: SessionDep,
    ip: ClientIP,
    format: Annotated[Literal["md", "json"], Query()] = "md",
) -> Response:
    """The conversation as a file: its messages and their sources, as you
    see them in Chat. Its starter's, or an admin's with the recorded reason
    (the same rule as opening it). The export is recorded, without its
    content."""
    out = await conversation_export.build(
        session,
        conv=ctx.conversation,
        assistant=ctx.assistant,
        membership=ctx.membership,
        fmt=format,
    )
    await audit.record(
        session,
        action="conversation.export",
        org_id=ctx.conversation.org_id,
        actor_user_id=ctx.membership.user_id,
        target_type="conversation",
        target_id=ctx.conversation.id,
        meta={"format": format, "messages": out.messages},
        ip=ip,
    )
    await session.commit()
    return Response(
        content=out.content,
        media_type=out.media_type,
        headers={"Content-Disposition": f'attachment; filename="{out.filename}"'},
    )


__all__ = ["router"]
