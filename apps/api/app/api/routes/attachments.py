"""Screenshots in chat (Phase 9.6): upload one into a conversation, and
read it back to show it. Sending it is part of posting a message
(`attachment_ids` on `POST /conversations/{id}/messages`).

The image is streamed by the API with the signed-in person's token, not
handed out as a presigned storage link: a link would make the browser
fetch from the store's own address, which only works when the bucket
allows the app's origin."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, File, Response, UploadFile, status

from app.api.deps import (
    ChatConversationStarterCtx,
    ChatReadableConversationCtx,
    CurrentUser,
    SessionDep,
    per_user_limit,
)
from app.api.errors import Conflict
from app.models.conversation import ConversationStatus
from app.schemas.attachment import AttachmentOut
from app.services import attachments as svc

router = APIRouter(tags=["conversations"])

#: Each upload is a write to storage: limited per person, like other uploads.
UPLOADS = per_user_limit("attachments", "rate_limit_heavy")


@router.post(
    "/conversations/{conversation_id}/attachments",
    dependencies=[UPLOADS],
    response_model=AttachmentOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_attachment(
    ctx: ChatConversationStarterCtx,
    user: CurrentUser,
    session: SessionDep,
    file: Annotated[UploadFile, File()],
) -> AttachmentOut:
    """Attach a screenshot (PNG, JPEG, WebP or GIF, up to 5 MB) to this
    conversation. It is sent with the next message that names it. Only the
    person who started the conversation can attach to it."""
    if ctx.conversation.status is ConversationStatus.archived:
        raise Conflict("This conversation is archived", code="conversation_archived")
    # One byte over the limit is enough to know it is too large.
    content = await file.read(svc.MAX_BYTES + 1)
    row = await svc.upload(session, ctx.conversation, user_id=user.id, content=content)
    return AttachmentOut.model_validate(row)


@router.get(
    "/conversations/{conversation_id}/attachments/{attachment_id}/content",
    response_class=Response,
    responses={200: {"content": {"image/*": {}}, "description": "The image itself."}},
)
async def attachment_content(
    attachment_id: uuid.UUID, ctx: ChatReadableConversationCtx, session: SessionDep
) -> Response:
    """One of this conversation's screenshots, for whoever may read the
    conversation. Served as the image type decided from its bytes at upload,
    inline, and never sniffed into anything else: nothing in it can run."""
    row = await svc.get(session, ctx.conversation, attachment_id)
    content = await svc.read(row)
    return Response(
        content=content,
        media_type=row.content_type,
        headers={
            "Content-Disposition": "inline",
            "X-Content-Type-Options": "nosniff",
            # Private: it is one person's conversation, never for a shared cache.
            "Cache-Control": f"private, max-age={svc.CACHE_SECONDS}",
        },
    )
