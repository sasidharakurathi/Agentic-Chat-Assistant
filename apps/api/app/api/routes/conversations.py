from __future__ import annotations

import contextlib
import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import StreamingResponse

from app.agent import interrupts
from app.agent.events import ErrorEvent, sse_frame
from app.api import ratelimit
from app.api.deps import (
    AssistantCtx,
    ClientIP,
    ConversationCtx,
    ConversationEditorCtx,
    CurrentUser,
    PageQuery,
    SessionDep,
)
from app.api.errors import Conflict
from app.config import settings
from app.db.session import get_sessionmaker
from app.logging import get_logger
from app.models.conversation import ConversationStatus
from app.schemas.common import Message, Page
from app.schemas.conversation import (
    ConversationCreate,
    ConversationDetail,
    ConversationRename,
    ConversationSummary,
    MessageIn,
    MessageOut,
    RunDetail,
    RunOut,
    RunStep,
)
from app.services import chat, trace

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
        session,
        assistant=ctx.assistant,
        user_id=ctx.membership.user_id,
        title=body.title,
        external_user_ref=body.external_user_ref,
    )
    return ConversationSummary.model_validate(conv)


@router.get("/assistants/{assistant_id}/conversations", response_model=Page[ConversationSummary])
async def list_conversations(
    ctx: AssistantCtx,
    session: SessionDep,
    page: PageQuery,
    external_user_ref: Annotated[
        str | None, Query(max_length=200, description="Only this end user's conversations.")
    ] = None,
) -> Page[ConversationSummary]:
    rows = await chat.list_conversations(
        session,
        ctx.assistant.id,
        limit=page.limit,
        cursor=page.cursor,
        external_user_ref=external_user_ref,
    )
    return Page(
        items=[ConversationSummary.model_validate(c) for c in rows.items],
        next_cursor=rows.next_cursor,
    )


#: Messages inlined in the conversation detail. The rest is one
#: `GET .../messages?cursor=` away. The detail used to inline every message
#: ever sent, so a long conversation got slower to open every turn.
DETAIL_MESSAGES = 50


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(ctx: ConversationCtx, session: SessionDep) -> ConversationDetail:
    page = await chat.list_messages(session, ctx.conversation.id, limit=DETAIL_MESSAGES)
    return ConversationDetail(
        **ConversationSummary.model_validate(ctx.conversation).model_dump(),
        messages=[MessageOut.model_validate(m) for m in page.items],
        messages_next_cursor=page.next_cursor,
    )


@router.get("/conversations/{conversation_id}/messages", response_model=Page[MessageOut])
async def list_messages(
    ctx: ConversationCtx, session: SessionDep, page: PageQuery
) -> Page[MessageOut]:
    """Older history, newest page first; items within a page are oldest-first."""
    rows = await chat.list_messages(
        session, ctx.conversation.id, limit=page.limit, cursor=page.cursor
    )
    return Page(
        items=[MessageOut.model_validate(m) for m in rows.items], next_cursor=rows.next_cursor
    )


@router.get("/conversations/{conversation_id}/runs", response_model=Page[RunOut])
async def list_runs(
    ctx: ConversationCtx,
    session: SessionDep,
    page: PageQuery,
    message_id: Annotated[
        uuid.UUID | None, Query(description="Only the run that produced this message.")
    ] = None,
) -> Page[RunOut]:
    rows = await chat.list_runs(
        session, ctx.conversation.id, message_id=message_id, limit=page.limit, cursor=page.cursor
    )
    return Page(items=[RunOut.model_validate(r) for r in rows.items], next_cursor=rows.next_cursor)


@router.get("/conversations/{conversation_id}/runs/{run_id}", response_model=RunDetail)
async def get_run(run_id: uuid.UUID, ctx: ConversationCtx, session: SessionDep) -> RunDetail:
    """The trace detail (plan §8): the run row plus the tool calls it made."""
    run, message = await chat.get_run(session, ctx.conversation.id, run_id)
    timeline, nodes, graph = await trace.build(session, run, message, ctx.conversation)
    return RunDetail(
        **RunOut.model_validate(run).model_dump(),
        steps=[RunStep.model_validate(s) for s in chat.run_steps(message)],
        assistant_id=ctx.conversation.assistant_id,
        timeline=timeline,
        nodes=nodes,
        graph=graph,
    )


@router.patch("/conversations/{conversation_id}", response_model=ConversationSummary)
async def rename_conversation(
    body: ConversationRename,
    ctx: ConversationEditorCtx,
    user: CurrentUser,
    session: SessionDep,
    ip: ClientIP,
) -> ConversationSummary:
    conv = await chat.rename(session, ctx.conversation, body.title, actor_user_id=user.id, ip=ip)
    return ConversationSummary.model_validate(conv)


@router.delete("/conversations/{conversation_id}", response_model=Message)
async def archive_conversation(
    ctx: ConversationEditorCtx, user: CurrentUser, session: SessionDep, ip: ClientIP
) -> Message:
    await chat.archive(session, ctx.conversation, actor_user_id=user.id, ip=ip)
    return Message(message="archived")


@router.post(
    "/conversations/{conversation_id}:interrupt",
    response_model=Message,
    status_code=status.HTTP_202_ACCEPTED,
)
async def interrupt_conversation(ctx: ConversationEditorCtx) -> Message:
    """Stop the turn running in this conversation (task 1.6).

    202, because the stop is a request: the turn ends on its own stream, which
    then closes with a normal `done`. It is published as well as tried
    locally, since the turn may be streaming from a different worker.
    Stopping a turn is changing the conversation, so the same rule applies as
    for posting into it: its creator, or an admin.
    """
    interrupts.interrupt_local(ctx.conversation.id)
    await interrupts.publish(ctx.conversation.id)
    return Message(message="interrupt requested")


@router.post("/conversations/{conversation_id}/messages")
async def post_message(
    body: MessageIn, ctx: ConversationEditorCtx, request: Request, ip: ClientIP
) -> StreamingResponse:
    if ctx.conversation.status is ConversationStatus.archived:
        # Refused before the stream opens, so it is a real 409 rather than an
        # error event inside a 200. An archived thread must not run the agent.
        raise Conflict("This conversation is archived", code="conversation_archived")
    # Before the stream opens too, so a caller that is going too fast gets a
    # real 429 (task 5.8): per person, and per org across its members.
    await ratelimit.enforce("chat_user", str(ctx.membership.user_id), settings.rate_limit_chat_user)
    await ratelimit.enforce("chat_org", str(ctx.conversation.org_id), settings.rate_limit_chat_org)
    conversation_id = ctx.conversation.id
    text = body.text

    async def gen() -> AsyncIterator[str]:
        sessionmaker = get_sessionmaker()
        async with sessionmaker() as s:
            try:
                # `aclosing`: on a disconnect the turn is closed *here*, while
                # its session is still open, so it can record itself as
                # aborted. Without it the abandoned generator is finalised
                # later by the GC — after this session has already closed.
                async with contextlib.aclosing(
                    chat.run_message(s, conversation_id=conversation_id, text=text)
                ) as events:
                    async for event in events:
                        if await request.is_disconnected():
                            log.info(
                                "sse_client_disconnected", conversation_id=str(conversation_id)
                            )
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
