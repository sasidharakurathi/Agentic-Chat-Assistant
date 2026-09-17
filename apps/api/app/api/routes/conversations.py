from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import APIRouter, Request, status
from fastapi.responses import StreamingResponse

from app.agent.events import ErrorEvent, sse_frame
from app.api.deps import AssistantCtx, ClientIP, ConversationCtx, SessionDep
from app.db.session import get_sessionmaker
from app.logging import get_logger
from app.schemas.common import Message
from app.schemas.conversation import (
    ConversationCreate,
    ConversationDetail,
    ConversationRename,
    ConversationSummary,
    MessageIn,
    MessageOut,
)
from app.services import chat

router = APIRouter(tags=["conversations"])
log = get_logger(__name__)


@router.post(
    "/assistants/{assistant_id}/conversations",
    response_model=ConversationSummary,
    status_code=status.HTTP_201_CREATED,
)
async def create_conversation(
    body: ConversationCreate, ctx: AssistantCtx, session: SessionDep
) -> ConversationSummary:
    conv = await chat.create_conversation(
        session, assistant=ctx.assistant, user_id=ctx.membership.user_id, title=body.title
    )
    return ConversationSummary.model_validate(conv)


@router.get("/assistants/{assistant_id}/conversations", response_model=list[ConversationSummary])
async def list_conversations(ctx: AssistantCtx, session: SessionDep) -> list[ConversationSummary]:
    rows = await chat.list_conversations(session, ctx.assistant.id)
    return [ConversationSummary.model_validate(c) for c in rows]


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(ctx: ConversationCtx, session: SessionDep) -> ConversationDetail:
    msgs = await chat.list_messages(session, ctx.conversation.id)
    return ConversationDetail(
        **ConversationSummary.model_validate(ctx.conversation).model_dump(),
        messages=[MessageOut.model_validate(m) for m in msgs],
    )


@router.patch("/conversations/{conversation_id}", response_model=ConversationSummary)
async def rename_conversation(
    body: ConversationRename, ctx: ConversationCtx, session: SessionDep
) -> ConversationSummary:
    conv = await chat.rename(session, ctx.conversation, body.title)
    return ConversationSummary.model_validate(conv)


@router.delete("/conversations/{conversation_id}", response_model=Message)
async def archive_conversation(ctx: ConversationCtx, session: SessionDep) -> Message:
    await chat.archive(session, ctx.conversation)
    return Message(message="archived")


@router.post("/conversations/{conversation_id}/messages")
async def post_message(
    body: MessageIn, ctx: ConversationCtx, request: Request, ip: ClientIP
) -> StreamingResponse:
    conversation_id = ctx.conversation.id
    text = body.text

    async def gen() -> AsyncIterator[str]:
        sessionmaker = get_sessionmaker()
        async with sessionmaker() as s:
            try:
                async for event in chat.run_message(s, conversation_id=conversation_id, text=text):
                    if await request.is_disconnected():
                        log.info("sse_client_disconnected", conversation_id=str(conversation_id))
                        break
                    yield sse_frame(event)
            except Exception as exc:
                log.exception("sse_stream_failed", conversation_id=str(conversation_id))
                yield sse_frame(ErrorEvent(code="internal_error", message=str(exc)))

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
