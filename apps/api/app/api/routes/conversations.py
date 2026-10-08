from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Path, Query, Request, status
from fastapi.responses import StreamingResponse

from app.agent import interrupts
from app.api import ratelimit
from app.api.deps import (
    AssistantCtx,
    ClientIP,
    ConversationCtx,
    ConversationStarterCtx,
    CurrentUser,
    PageQuery,
    SessionDep,
)
from app.api.errors import Conflict, Forbidden, NotFound
from app.config import settings
from app.logging import get_logger
from app.models.conversation import ConversationStatus
from app.observability import metrics
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
    TurnState,
)
from app.services import chat, trace, turns

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
    # A conversation started *as* an end user reads and writes that user's
    # memory (task 6.3). Reading it directly is for editors
    # (`routes/memories.py`), so starting one is too: a member could
    # otherwise name any end user and ask the assistant what it remembers.
    if body.external_user_ref is not None and not ctx.can_edit:
        raise Forbidden(
            "Only this assistant's editors can start a conversation on behalf of an end user",
            code="not_assistant_editor",
        )
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
    running = await turns.running_among([c.id for c in rows.items])
    return Page(
        items=[
            ConversationSummary.model_validate(c).model_copy(update={"running": c.id in running})
            for c in rows.items
        ],
        next_cursor=rows.next_cursor,
    )


#: Messages inlined in the conversation detail. The rest is one
#: `GET .../messages?cursor=` away. The detail used to inline every message
#: ever sent, so a long conversation got slower to open every turn.
DETAIL_MESSAGES = 50


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(ctx: ConversationCtx, session: SessionDep) -> ConversationDetail:
    page = await chat.list_messages(session, ctx.conversation.id, limit=DETAIL_MESSAGES)
    turn_id = await turns.running_turn(ctx.conversation.id)
    summary = ConversationSummary.model_validate(ctx.conversation).model_dump()
    return ConversationDetail(
        **{**summary, "running": turn_id is not None},
        messages=[MessageOut.model_validate(m) for m in page.items],
        messages_next_cursor=page.next_cursor,
        turn_id=turn_id,
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
    ctx: ConversationStarterCtx,
    user: CurrentUser,
    session: SessionDep,
    ip: ClientIP,
) -> ConversationSummary:
    conv = await chat.rename(session, ctx.conversation, body.title, actor_user_id=user.id, ip=ip)
    return ConversationSummary.model_validate(conv)


@router.delete("/conversations/{conversation_id}", response_model=Message)
async def archive_conversation(
    ctx: ConversationStarterCtx, user: CurrentUser, session: SessionDep, ip: ClientIP
) -> Message:
    await chat.archive(session, ctx.conversation, actor_user_id=user.id, ip=ip)
    return Message(message="archived")


@router.post(
    "/conversations/{conversation_id}:interrupt",
    response_model=Message,
    status_code=status.HTTP_202_ACCEPTED,
)
async def interrupt_conversation(ctx: ConversationStarterCtx) -> Message:
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
    body: MessageIn,
    ctx: ConversationStarterCtx,
    session: SessionDep,
    request: Request,
    ip: ClientIP,
) -> StreamingResponse:
    if ctx.conversation.status is ConversationStatus.archived:
        # Refused before the stream opens, so it is a real 409 rather than an
        # error event inside a 200. An archived thread must not run the agent.
        raise Conflict("This conversation is archived", code="conversation_archived")
    # Before the stream opens too, so a caller that is going too fast gets a
    # real 429 (task 5.8): per person, and per org across its members.
    await ratelimit.enforce("chat_user", str(ctx.membership.user_id), settings.rate_limit_chat_user)
    await ratelimit.enforce("chat_org", str(ctx.conversation.org_id), settings.rate_limit_chat_org)
    # The request's own session did its work (who is asking, may they). It
    # is closed only when the response ends, which for a stream is the whole
    # turn: without this, every open chat held a second idle connection.
    await chat.release(session)
    # The turn runs on the server from here on (QOS-01): this response only
    # watches it. Leaving (a reload, another conversation, a dropped
    # network) no longer ends it; Stop does.
    try:
        turn_id = await turns.start(ctx.conversation.id, body.text)
    except turns.TurnInProgress:
        raise Conflict(
            "An answer is still being written in this conversation. "
            "Wait for it, or stop it, then send your message.",
            code="turn_in_progress",
        ) from None
    return _watch(ctx.conversation.id, turn_id, after=None)


def _watch(conversation_id: uuid.UUID, turn_id: str, *, after: str | None) -> StreamingResponse:
    """A turn's events as server-sent events, from after `after` (or the
    start) until it ends. Each carries its id, to pass back as `after` (or
    `Last-Event-ID`) when watching again."""

    async def gen() -> AsyncIterator[str]:
        opened = time.perf_counter()
        try:
            async for event_id, frame in turns.follow(conversation_id, turn_id, after):
                yield f"id: {event_id}\n{frame}"
        finally:
            # However it ended: the turn finished, or the watcher left.
            metrics.STREAM_DURATION.observe(time.perf_counter() - opened)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Turn-Id": turn_id,
        },
    )


@router.get("/conversations/{conversation_id}/turn", response_model=TurnState)
async def get_turn(ctx: ConversationCtx) -> TurnState:
    """The turn running in this conversation, if any (QOS-01). A page that
    opens a conversation asks this, and attaches to the answer being
    written instead of showing nothing until it is saved."""
    return TurnState(turn_id=await turns.running_turn(ctx.conversation.id))


@router.get("/conversations/{conversation_id}/turns/{turn_id}/events")
async def watch_turn(
    turn_id: Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")],
    ctx: ConversationCtx,
    request: Request,
    after: Annotated[
        str | None, Query(max_length=40, description="The id of the last event already seen.")
    ] = None,
) -> StreamingResponse:
    """Watch a turn of this conversation: everything it has said so far
    (after `after`, or `Last-Event-ID`), then the rest as it comes, until it
    ends. Finished turns stay readable for a few minutes. Anyone who may see
    the conversation may watch."""
    if not await turns.known(ctx.conversation.id, turn_id):
        raise NotFound("No such turn in this conversation", code="turn_not_found")
    since = after or request.headers.get("last-event-id") or None
    return _watch(ctx.conversation.id, turn_id, after=since)
