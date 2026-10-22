from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Path, Query, Request, status
from fastapi.responses import StreamingResponse

from app.agent import interrupts
from app.api import ratelimit
from app.api.deps import (
    ChatAssistantCtx,
    ChatConversationStarterCtx,
    ChatReadableConversationCtx,
    ClientIP,
    ConversationContext,
    CurrentUser,
    PageQuery,
    ReadableConversationCtx,
    SessionDep,
)
from app.api.errors import Conflict, Forbidden, NotFound
from app.config import settings
from app.logging import get_logger
from app.models.assistant import AssistantStatus
from app.models.conversation import Conversation, ConversationMode, ConversationStatus
from app.models.enums import MemberRole
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
from app.schemas.feedback import FeedbackIn, FeedbackOut, OutcomeIn, OutcomeOut
from app.services import (
    attachments,
    audit,
    chat,
    chat_view,
    feedback,
    knowledge_access,
    listing,
    privacy,
    trace,
    turns,
)

router = APIRouter(tags=["conversations"])
log = get_logger(__name__)


@router.post(
    "/assistants/{assistant_id}/conversations",
    response_model=ConversationSummary,
    status_code=status.HTTP_201_CREATED,
)
async def create_conversation(
    body: ConversationCreate, ctx: ChatAssistantCtx, session: SessionDep
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
        mode=body.mode,
        version_number=body.version,
        can_edit=ctx.can_edit,
    )
    out = ConversationSummary.model_validate(conv)
    return chat_view.summary(out) if chat_view.shaped(ctx.membership) else out


@router.get("/assistants/{assistant_id}/conversations", response_model=Page[ConversationSummary])
async def list_conversations(
    ctx: ChatAssistantCtx,
    session: SessionDep,
    page: PageQuery,
    external_user_ref: Annotated[
        str | None, Query(max_length=200, description="Only this end user's conversations.")
    ] = None,
    mode: Annotated[
        ConversationMode, Query(description="`preview` lists the Studio's test chats.")
    ] = ConversationMode.live,
    everyone: Annotated[
        bool, Query(description="Everyone's conversations, not only yours (admins).")
    ] = False,
) -> Page[ConversationSummary]:
    """The caller's own conversations: they are private (Phase 7b.2, plan
    D5). An admin may list everyone's, with other people's titles left out:
    a title is the start of a first message."""
    if mode is ConversationMode.preview and not ctx.can_edit:
        raise Forbidden(
            "Only this assistant's builders and admins can see its previews",
            code="not_assistant_editor",
        )
    me = ctx.membership.user_id
    if everyone and not ctx.membership.role.satisfies(MemberRole.admin):
        raise Forbidden(
            "Only admins can list other people's conversations", code="insufficient_role"
        )
    rows = await chat.list_conversations(
        session,
        ctx.assistant.id,
        limit=page.limit,
        cursor=page.cursor,
        external_user_ref=external_user_ref,
        mode=mode,
        started_by=None if everyone else me,
    )
    running = await turns.running_among([c.id for c in rows.items])
    items = [_summary(c, me, running=c.id in running) for c in rows.items]
    if chat_view.shaped(ctx.membership):
        items = [chat_view.summary(i) for i in items]
    return Page(items=items, next_cursor=rows.next_cursor)


def _summary(c: Conversation, me: uuid.UUID, *, running: bool) -> ConversationSummary:
    out = ConversationSummary.model_validate(c).model_copy(update={"running": running})
    if c.created_by != me:
        out = out.model_copy(update={"title": "Conversation", "title_hidden": True})
    return out


#: Messages inlined in the conversation detail. The rest is one
#: `GET .../messages?cursor=` away. The detail used to inline every message
#: ever sent, so a long conversation got slower to open every turn.
DETAIL_MESSAGES = 50


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    ctx: ChatReadableConversationCtx, session: SessionDep
) -> ConversationDetail:
    page = await chat.list_messages(session, ctx.conversation.id, limit=DETAIL_MESSAGES)
    turn_id = await turns.running_turn(ctx.conversation.id)
    summary = ConversationSummary.model_validate(ctx.conversation)
    if chat_view.shaped(ctx.membership):
        summary = chat_view.summary(summary)
    rated, solved = await feedback.of_conversation(session, ctx.conversation)
    return ConversationDetail(
        **{**summary.model_dump(), "running": turn_id is not None},
        messages=await _messages(session, ctx, page.items),
        messages_next_cursor=page.next_cursor,
        turn_id=turn_id,
        feedback={k: FeedbackOut(rating=r, comment=c) for k, (r, c) in rated.items()},
        solved=solved,
    )


async def _messages(
    session: SessionDep, ctx: ConversationContext, rows: list[Any]
) -> list[MessageOut]:
    # Citations of sources the reader may no longer read stay hidden (Phase
    # 9.4): the answer was theirs, the source isn't any more.
    allowed = await knowledge_access.readable_ids(session, ctx.assistant, ctx.membership)
    lost = await knowledge_access.lost_any(session, ctx.assistant.id, allowed)
    out = [
        MessageOut.model_validate(m).model_copy(update=knowledge_access.shown(m, allowed, lost))
        for m in rows
    ]
    return [chat_view.message(m) for m in out] if chat_view.shaped(ctx.membership) else out


@router.get("/conversations/{conversation_id}/messages", response_model=Page[MessageOut])
async def list_messages(
    ctx: ChatReadableConversationCtx, session: SessionDep, page: PageQuery
) -> Page[MessageOut]:
    """Older history, newest page first; items within a page are oldest-first."""
    rows = await chat.list_messages(
        session, ctx.conversation.id, limit=page.limit, cursor=page.cursor
    )
    return Page(items=await _messages(session, ctx, list(rows.items)), next_cursor=rows.next_cursor)


@router.get("/conversations/{conversation_id}/runs", response_model=Page[RunOut])
async def list_runs(
    ctx: ReadableConversationCtx,
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
async def get_run(
    run_id: uuid.UUID, ctx: ReadableConversationCtx, session: SessionDep
) -> RunDetail:
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
    ctx: ChatConversationStarterCtx,
    user: CurrentUser,
    session: SessionDep,
    ip: ClientIP,
) -> ConversationSummary:
    conv = await chat.rename(session, ctx.conversation, body.title, actor_user_id=user.id, ip=ip)
    out = ConversationSummary.model_validate(conv)
    return chat_view.summary(out) if chat_view.shaped(ctx.membership) else out


@router.delete("/conversations/{conversation_id}", response_model=Message)
async def archive_conversation(
    ctx: ChatConversationStarterCtx, user: CurrentUser, session: SessionDep, ip: ClientIP
) -> Message:
    await chat.archive(session, ctx.conversation, actor_user_id=user.id, ip=ip)
    return Message(message="archived")


@router.post("/conversations/{conversation_id}:restore", response_model=ConversationSummary)
async def restore_conversation(
    ctx: ChatConversationStarterCtx, user: CurrentUser, session: SessionDep, ip: ClientIP
) -> ConversationSummary:
    """Put a conversation removed from your history back (Phase 8.8). It was
    kept all along; now it is in your lists again, and can be continued."""
    conv = ctx.conversation
    if conv.status is ConversationStatus.archived:
        conv.status = ConversationStatus.active
        await audit.record(
            session,
            action="conversation.restore",
            org_id=conv.org_id,
            actor_user_id=user.id,
            target_type="conversation",
            target_id=conv.id,
            ip=ip,
        )
        await session.commit()
    out = ConversationSummary.model_validate(conv)
    return chat_view.summary(out) if chat_view.shaped(ctx.membership) else out


@router.put(
    "/conversations/{conversation_id}/messages/{message_id}/feedback",
    response_model=FeedbackOut,
)
async def rate_answer(
    message_id: uuid.UUID,
    body: FeedbackIn,
    ctx: ChatConversationStarterCtx,
    user: CurrentUser,
    session: SessionDep,
) -> FeedbackOut:
    """Your thumbs on one answer, with an optional comment (Phase 8.8). The
    assistant's builders see the comment with that question and answer,
    without your name. `rating: null` takes it back."""
    row = await feedback.rate(
        session,
        ctx.conversation,
        user_id=user.id,
        message_id=message_id,
        rating=body.rating,
        comment=body.comment,
    )
    if row is None:
        return FeedbackOut()
    return FeedbackOut(rating=row.rating, comment=row.comment)


@router.put("/conversations/{conversation_id}/outcome", response_model=OutcomeOut)
async def conversation_outcome(
    body: OutcomeIn, ctx: ChatConversationStarterCtx, session: SessionDep
) -> OutcomeOut:
    """ "Did this solve it?" (Phase 8.8): once per conversation, changeable.
    Kept without your name, even when the conversation goes."""
    row = await feedback.solve(session, ctx.conversation, solved=body.solved)
    return OutcomeOut(solved=row.solved)


@router.post(
    "/conversations/{conversation_id}:interrupt",
    response_model=Message,
    status_code=status.HTTP_202_ACCEPTED,
)
async def interrupt_conversation(ctx: ChatConversationStarterCtx) -> Message:
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
    ctx: ChatConversationStarterCtx,
    session: SessionDep,
    request: Request,
    ip: ClientIP,
) -> StreamingResponse:
    if ctx.conversation.status is ConversationStatus.archived:
        # Refused before the stream opens, so it is a real 409 rather than an
        # error event inside a 200. An archived thread must not run the agent.
        raise Conflict("This conversation is archived", code="conversation_archived")
    # An eval run's conversation belongs to the run (Phase 9.9): replying in
    # it skipped the audience, the pause and the person's limits.
    if ctx.conversation.eval_run_id is not None:
        raise Forbidden(
            "This conversation belongs to an eval run and can't be replied in",
            code="eval_conversation",
        )
    # Refused here too, for the same reason (Phase 7b.1): a paused assistant,
    # one with nothing approved, or a preview its starter may no longer run.
    if ctx.conversation.mode is ConversationMode.preview:
        if not ctx.can_edit_assistant:
            raise Forbidden(
                "Only this assistant's builders and admins can preview it",
                code="not_assistant_editor",
            )
        if ctx.assistant.status is AssistantStatus.archived:
            raise Conflict(
                "This assistant has been paused by an admin", code="assistant_unavailable"
            )
    elif ctx.conversation.eval_run_id is None and (why := chat.unavailable(ctx.assistant)):
        raise why
    elif ctx.conversation.eval_run_id is None and not await listing.can_see(
        session, ctx.assistant, ctx.membership
    ):
        # Dropped from its audience (Phase 8.5): their history stays
        # readable; the assistant no longer answers them.
        raise Forbidden("This assistant is no longer available to you", code="not_in_audience")
    if (
        ctx.conversation.mode is ConversationMode.live
        and ctx.conversation.eval_run_id is None
        and not await privacy.has_accepted(session, ctx.membership)
    ):
        # Phase 8.7: how Chat uses your data, read and accepted first.
        raise Forbidden(
            "Read how Chat uses your data, and accept it, before your first message",
            code="notice_required",
        )
    # Before the stream opens too, so a caller that is going too fast gets a
    # real 429 (task 5.8): per person, and per org across its members.
    # An Employee's bucket is smaller (Phase 8.9).
    if ctx.membership.role is MemberRole.employee:
        await ratelimit.enforce(
            "chat_employee", str(ctx.membership.user_id), settings.rate_limit_chat_employee
        )
    else:
        await ratelimit.enforce(
            "chat_user", str(ctx.membership.user_id), settings.rate_limit_chat_user
        )
    await ratelimit.enforce("chat_org", str(ctx.conversation.org_id), settings.rate_limit_chat_org)
    # Screenshots sent with it (Phase 9.6): this conversation's, and not
    # sent before. Refused here, as a real 400, before the turn starts.
    attachment_ids = await attachments.check_unsent(session, ctx.conversation, body.attachment_ids)
    # The request's own session did its work (who is asking, may they). It
    # is closed only when the response ends, which for a stream is the whole
    # turn: without this, every open chat held a second idle connection.
    await chat.release(session)
    # The turn runs on the server from here on (QOS-01): this response only
    # watches it. Leaving (a reload, another conversation, a dropped
    # network) no longer ends it; Stop does.
    try:
        turn_id = await turns.start(ctx.conversation.id, body.text, attachment_ids=attachment_ids)
    except turns.TurnInProgress:
        raise Conflict(
            "An answer is still being written in this conversation. "
            "Wait for it, or stop it, then send your message.",
            code="turn_in_progress",
        ) from None
    return _watch(ctx.conversation.id, turn_id, after=None, shaped=chat_view.shaped(ctx.membership))


def _watch(
    conversation_id: uuid.UUID, turn_id: str, *, after: str | None, shaped: bool = False
) -> StreamingResponse:
    """A turn's events as server-sent events, from after `after` (or the
    start) until it ends. Each carries its id, to pass back as `after` (or
    `Last-Event-ID`) when watching again. `shaped`: for an employee, the
    working parts are left out (`services/chat_view`, Phase 8.1); an event
    left out still moves the id on, so watching again skips it too."""

    async def gen() -> AsyncIterator[str]:
        opened = time.perf_counter()
        try:
            async for event_id, raw in turns.follow(conversation_id, turn_id, after):
                if not event_id:
                    # A keep-alive (Phase 8.6): nothing to resume from.
                    yield raw
                    continue
                frame = chat_view.frame(raw) if shaped else raw
                if frame is None:
                    continue
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
async def get_turn(ctx: ChatReadableConversationCtx) -> TurnState:
    """The turn running in this conversation, if any (QOS-01). A page that
    opens a conversation asks this, and attaches to the answer being
    written instead of showing nothing until it is saved."""
    return TurnState(turn_id=await turns.running_turn(ctx.conversation.id))


@router.get("/conversations/{conversation_id}/turns/{turn_id}/events")
async def watch_turn(
    turn_id: Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")],
    ctx: ChatReadableConversationCtx,
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
    return _watch(
        ctx.conversation.id, turn_id, after=since, shaped=chat_view.shaped(ctx.membership)
    )
