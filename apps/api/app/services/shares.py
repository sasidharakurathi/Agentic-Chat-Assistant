"""Read-only conversation links, inside the organisation (Phase 9.4).

The person who started a conversation can share it. The link opens a
read-only copy for a signed-in member of the same organisation when all of
these hold, checked at every opening:

- the link wasn't revoked, and the person who shared it is still a member
  (a leaver's links stop opening);
- the conversation is a live one: previews are never shared;
- the reader may use the assistant (`listing.can_see`, which includes its
  audience and its restricted knowledge);
- the reader may read every source the conversation cites.

A refusal says no more than "this link doesn't open for you". What opens is
what Chat shows an employee: the messages and their citations, never the
tool calls, statements or results, which were the asker's own data. Every
opening is in the audit log. Deleting or erasing the conversation deletes
its links.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError, BadRequest, Forbidden, NotFound
from app.config import settings
from app.models.assistant import Assistant
from app.models.conversation import (
    Conversation,
    ConversationMode,
    ConversationShare,
    Message,
    MessageRole,
)
from app.models.user import User
from app.schemas.conversation import MessageOut
from app.schemas.share import ShareCreated, SharedConversationOut, ShareOut
from app.services import audit, chat_view, knowledge_access, listing

#: The most links one conversation keeps open at once.
MAX_OPEN = 10
#: The most messages a shared copy shows, the newest.
MAX_MESSAGES = 200
GONE = "This link doesn't open any more."


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def link(token: str) -> str:
    return f"{settings.app_base_url.rstrip('/')}/shared/{token}"


async def create(
    session: AsyncSession, conversation: Conversation, *, user_id: uuid.UUID, ip: str | None
) -> ShareCreated:
    if conversation.mode is ConversationMode.preview or conversation.eval_run_id is not None:
        raise BadRequest("A preview can't be shared", code="preview_not_shared")
    open_now = await session.scalars(
        select(ConversationShare.id).where(
            ConversationShare.conversation_id == conversation.id,
            ConversationShare.revoked_at.is_(None),
        )
    )
    if len(open_now.all()) >= MAX_OPEN:
        raise BadRequest(
            f"This conversation already has {MAX_OPEN} open links: revoke one first",
            code="too_many_links",
        )
    token = secrets.token_urlsafe(32)
    share = ConversationShare(
        org_id=conversation.org_id,
        conversation_id=conversation.id,
        created_by=user_id,
        token_hash=_hash(token),
    )
    session.add(share)
    await session.flush()
    await audit.record(
        session,
        action="conversation.share_create",
        org_id=conversation.org_id,
        actor_user_id=user_id,
        target_type="conversation",
        target_id=conversation.id,
        meta={"share_id": str(share.id)},
        ip=ip,
    )
    await session.commit()
    await session.refresh(share)
    return ShareCreated(id=share.id, url=link(token), created_at=share.created_at)


async def list_for(session: AsyncSession, conversation: Conversation) -> list[ShareOut]:
    rows = await session.scalars(
        select(ConversationShare)
        .where(ConversationShare.conversation_id == conversation.id)
        .order_by(ConversationShare.created_at.desc())
    )
    return [ShareOut.model_validate(r) for r in rows.all()]


async def revoke(
    session: AsyncSession,
    conversation: Conversation,
    share_id: uuid.UUID,
    *,
    user_id: uuid.UUID,
    ip: str | None,
) -> ShareOut:
    share = await session.scalar(
        select(ConversationShare).where(
            ConversationShare.id == share_id,
            ConversationShare.conversation_id == conversation.id,
        )
    )
    if share is None:
        raise NotFound("Link not found")
    if share.revoked_at is None:
        share.revoked_at = datetime.now(UTC)
        await audit.record(
            session,
            action="conversation.share_revoke",
            org_id=conversation.org_id,
            actor_user_id=user_id,
            target_type="conversation",
            target_id=conversation.id,
            meta={"share_id": str(share.id)},
            ip=ip,
        )
        await session.commit()
        await session.refresh(share)
    return ShareOut.model_validate(share)


async def _still_member(session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    from app.security.access import member_of

    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        return False
    try:
        await member_of(session, org_id, user_id, missing="gone", chat=True)
    except AppError:
        return False
    return True


async def open_link(
    session: AsyncSession, token: str, *, user: User, ip: str | None
) -> SharedConversationOut:
    """The shared copy, for this reader, or a refusal that says no more
    than it must."""
    from app.security.access import member_of

    share = await session.scalar(
        select(ConversationShare).where(ConversationShare.token_hash == _hash(token))
    )
    if share is None or share.revoked_at is not None:
        raise NotFound(GONE, code="share_gone")
    try:
        reader = await member_of(session, share.org_id, user.id, missing="gone", chat=True)
    except AppError as exc:
        raise NotFound(GONE, code="share_gone") from exc
    conversation = await session.get(Conversation, share.conversation_id)
    if (
        conversation is None
        or conversation.mode is ConversationMode.preview
        or share.created_by is None
        or not await _still_member(session, share.org_id, share.created_by)
    ):
        raise NotFound(GONE, code="share_gone")
    assistant = await session.get(Assistant, conversation.assistant_id)
    refused = Forbidden(
        "This conversation uses an assistant or knowledge you can't use, so it doesn't "
        "open for you.",
        code="share_not_for_you",
    )
    if assistant is None or not await listing.can_see(session, assistant, reader):
        raise refused
    rows = list(
        (
            await session.scalars(
                select(Message)
                .where(
                    Message.conversation_id == conversation.id,
                    Message.role.in_((MessageRole.user, MessageRole.assistant)),
                )
                .order_by(Message.created_at.desc())
                .limit(MAX_MESSAGES)
            )
        ).all()
    )[::-1]
    allowed = await knowledge_access.readable_ids(session, assistant, reader)
    cited = knowledge_access.cited_sources(m.blocks for m in rows)
    if allowed is not None and not cited <= allowed:
        raise refused
    # An answer can use a passage without citing it (citations off): the
    # version it was given on decides too (Phase 9.9).
    if allowed is not None and not await knowledge_access.reads_version(
        session, assistant, conversation.assistant_version_id, allowed
    ):
        raise refused

    share.open_count += 1
    share.last_opened_at = datetime.now(UTC)
    await audit.record(
        session,
        action="conversation.share_opened",
        org_id=share.org_id,
        actor_user_id=user.id,
        target_type="conversation",
        target_id=conversation.id,
        meta={"share_id": str(share.id)},
        ip=ip,
    )
    await session.commit()
    sharer = await session.get(User, share.created_by)
    messages = []
    for m in rows:
        # What Chat shows an employee, and only the citations of it.
        shown = chat_view.message(MessageOut.model_validate(m))
        blocks = [b for b in shown.blocks if isinstance(b, dict) and b.get("type") == "citation"]
        messages.append(shown.model_copy(update={"blocks": blocks}))
    return SharedConversationOut(
        title=conversation.title,
        assistant_id=assistant.id,
        assistant_name=assistant.name,
        shared_by=(sharer.name or sharer.email) if sharer else "",
        shared_at=share.created_at,
        messages=messages,
    )


__all__ = ["MAX_OPEN", "create", "link", "list_for", "open_link", "revoke"]
