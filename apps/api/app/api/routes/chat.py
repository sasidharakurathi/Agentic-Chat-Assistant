"""Chat's own routes (Phase 8.1): the assistants a person can talk to, and
their conversations, for every role the Employee role included. Each
answers only with what Chat shows; the Studio's routes stay closed to
employees."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from app.api.deps import (
    ChatAssistantCtx,
    ChatMembership,
    ClientIP,
    PageQuery,
    SessionDep,
    require_role,
)
from app.config import settings
from app.db.pagination import keyset_page
from app.models.assistant import Assistant, AssistantStatus, AssistantVersion
from app.models.conversation import Conversation, ConversationMode, ConversationStatus, Message
from app.models.enums import MemberRole
from app.models.membership import Membership
from app.models.organization import Organization
from app.schemas.chat import (
    ChatAssistantOut,
    ChatConversationOut,
    ChatHelpOut,
)
from app.schemas.common import Page
from app.schemas.privacy import (
    AccessReadOut,
    AssistantAboutOut,
    NoticeAccept,
    NoticeOut,
    ProcessorOut,
    UseOut,
)
from app.security.access import can_edit_assistant
from app.services import listing as listing_service
from app.services import org_settings, privacy

router = APIRouter(tags=["chat"])

RequireAdmin = Depends(require_role(MemberRole.admin))


async def _version_numbers(session: SessionDep, ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not ids:
        return {}
    rows = await session.execute(
        select(AssistantVersion.id, AssistantVersion.version_number).where(
            AssistantVersion.id.in_(ids)
        )
    )
    out: dict[uuid.UUID, int] = {}
    for vid, number in rows.all():
        out[vid] = number
    return out


async def _card(session: SessionDep, a: Assistant, membership: Membership) -> ChatAssistantOut:
    """An assistant as Chat shows it: its live version's listing (Phase
    8.5), so what people see is what was reviewed."""
    shown = await listing_service.live_listing(session, a)
    numbers = await _version_numbers(
        session, [a.current_version_id] if a.current_version_id else []
    )
    return ChatAssistantOut(
        id=a.id,
        name=shown.name or a.name,
        description=shown.description,
        status=a.status,
        current_version_id=a.current_version_id,
        version_number=numbers.get(a.current_version_id) if a.current_version_id else None,
        can_edit=await can_edit_assistant(session, a, membership),
        category=shown.category,
        starters=shown.starters,
        official=a.official_at is not None,
    )


@router.get("/chat/assistants", response_model=list[ChatAssistantOut])
async def list_chat_assistants(
    membership: ChatMembership,
    session: SessionDep,
    q: Annotated[str | None, Query(max_length=100)] = None,
    category: Annotated[str | None, Query(max_length=40)] = None,
) -> list[ChatAssistantOut]:
    """The assistants that answer in Chat for this person (Phase 8.5): approved,
    not paused, and in their audience. Official ones first, then by name;
    `q` searches the name and description, `category` narrows."""
    assistants = (
        await session.scalars(
            select(Assistant)
            .where(
                Assistant.org_id == membership.org_id,
                Assistant.status == AssistantStatus.published,
            )
            .order_by(Assistant.name)
        )
    ).all()
    cards = [
        await _card(session, a, membership)
        for a in assistants
        if await listing_service.can_see(session, a, membership)
    ]
    if q and q.strip():
        needle = q.strip().lower()
        cards = [c for c in cards if needle in c.name.lower() or needle in c.description.lower()]
    if category:
        cards = [c for c in cards if c.category.lower() == category.strip().lower()]
    return sorted(cards, key=lambda c: (not c.official, c.name.lower()))


@router.get("/chat/assistants/{assistant_id}", response_model=ChatAssistantOut)
async def get_chat_assistant(ctx: ChatAssistantCtx, session: SessionDep) -> ChatAssistantOut:
    """One assistant as Chat shows it. A builder may open their own draft's
    card too, for its preview; an employee only one that answers."""
    return await _card(session, ctx.assistant, ctx.membership)


@router.get("/chat/assistants/{assistant_id}/about", response_model=AssistantAboutOut)
async def about_chat_assistant(ctx: ChatAssistantCtx, session: SessionDep) -> AssistantAboutOut:
    """An assistant's card (Phase 8.7): who runs it, when it was approved,
    what it looks up and may do, whether it remembers, and what leaves the
    building. From the approved version only: what an admin reviewed."""
    shown = await listing_service.live_listing(session, ctx.assistant)
    card = await privacy.about(session, ctx.assistant)
    return AssistantAboutOut(
        name=shown.name or ctx.assistant.name,
        description=shown.description,
        owner=card.owner,
        version_number=card.version_number,
        approved_at=card.approved_at,
        approved_by=card.approved_by,
        knowledge=card.knowledge,
        lookups=[UseOut(label=u.label, approval=u.approval) for u in card.lookups],
        actions=[UseOut(label=u.label, approval=u.approval) for u in card.actions],
        memory_tool=card.memory_tool,
        keeps_history=card.keeps_history,
        leaves=[ProcessorOut(name=p.name, what=p.what) for p in card.leaves],
    )


async def _notice_out(session: SessionDep, membership: Membership) -> NoticeOut:
    notice = await privacy.current(session, membership.org_id)
    return NoticeOut(
        version=notice.version,
        body=notice.body,
        updated_at=notice.updated_at,
        contact=notice.contact,
        processors=[ProcessorOut(name=p.name, what=p.what) for p in privacy.processors()],
        retention_days=(await org_settings.switches(session, membership.org_id)).retention_days,
        accepted_at=await privacy.accepted_at(
            session, membership.org_id, membership.user_id, notice.version
        ),
        required=settings.privacy_notice_required,
    )


@router.get("/chat/notice", response_model=NoticeOut)
async def chat_notice(membership: ChatMembership, session: SessionDep) -> NoticeOut:
    """How Chat uses your data (Phase 8.7), and whether you accepted the
    version in force: Chat waits for it before your first message."""
    return await _notice_out(session, membership)


@router.post("/chat/notice:accept", response_model=NoticeOut)
async def accept_chat_notice(
    body: NoticeAccept, membership: ChatMembership, session: SessionDep, ip: ClientIP
) -> NoticeOut:
    """Accept the version you read. An older one than the version in force
    is refused (`notice_changed`): read the new one."""
    await privacy.accept(session, membership, body.version, ip)
    return await _notice_out(session, membership)


@router.get("/chat/access", response_model=Page[AccessReadOut])
async def my_conversation_reads(
    membership: ChatMembership, session: SessionDep, page: PageQuery
) -> Page[AccessReadOut]:
    """Each time an admin opened one of your conversations (Phase 8.7):
    when, who, and the reason they gave. Newest first."""
    rows = await privacy.reads_of(session, membership, limit=page.limit, cursor=page.cursor)
    return Page(
        items=[
            AccessReadOut(
                conversation_id=r.conversation_id,
                conversation_title=r.conversation_title,
                read_at=r.read_at,
                reader=r.reader,
                reader_role=r.reader_role,
                reason=r.reason,
            )
            for r in rows.items
        ],
        next_cursor=rows.next_cursor,
    )


@router.get("/chat/help", response_model=ChatHelpOut)
async def chat_help(membership: ChatMembership, session: SessionDep) -> ChatHelpOut:
    """What the app keeps for when it can't reach the server (Phase 8.6):
    the organisation's own steps for getting on its network or VPN."""
    org = await session.get(Organization, membership.org_id)
    assert org is not None  # the membership above is in it
    return ChatHelpOut(
        organisation=org.name, offline_help=org_settings.switches_of(org).offline_help
    )


@router.get("/chat/conversations", response_model=Page[ChatConversationOut])
async def list_my_conversations(
    membership: ChatMembership,
    session: SessionDep,
    page: PageQuery,
    q: Annotated[str | None, Query(max_length=200)] = None,
    removed: bool = False,
) -> Page[ChatConversationOut]:
    """The caller's own conversations, with every assistant, newest first.
    Their own only: conversations are private (7b.2). Phase 8.8: `q` finds
    words in a title or anywhere in the conversation; `removed` lists the
    ones taken out of your history instead, to put back."""
    stmt = select(Conversation).where(
        Conversation.org_id == membership.org_id,
        Conversation.created_by == membership.user_id,
        Conversation.mode == ConversationMode.live,
        (Conversation.status == ConversationStatus.archived)
        if removed
        else (Conversation.status != ConversationStatus.archived),
        Conversation.eval_run_id.is_(None),
    )
    if q and q.strip():
        like = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        stmt = stmt.where(
            Conversation.title.ilike(like, escape="\\")
            | Conversation.id.in_(
                select(Message.conversation_id).where(
                    Message.org_id == membership.org_id,
                    Message.content.ilike(like, escape="\\"),
                )
            )
        )
    rows = await keyset_page(
        session,
        stmt,
        [Conversation.created_at, Conversation.id],
        limit=page.limit,
        cursor=page.cursor,
    )
    names = {
        a.id: a.name
        for a in (
            await session.scalars(
                select(Assistant).where(Assistant.id.in_({c.assistant_id for c in rows.items}))
            )
        ).all()
    }
    return Page(
        items=[
            ChatConversationOut(
                id=c.id,
                assistant_id=c.assistant_id,
                assistant_name=names.get(c.assistant_id, "An assistant"),
                title=c.title,
                created_at=c.created_at,
                last_message_at=c.last_message_at,
            )
            for c in rows.items
        ],
        next_cursor=rows.next_cursor,
    )


__all__ = ["router"]
