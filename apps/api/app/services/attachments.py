"""Screenshots in chat (Phase 9.6): the most common helpdesk input.

A person pastes or picks an image in the composer. It is uploaded into the
conversation straight away (`upload`), shown as a thumbnail, and sent with
the next message (`check_unsent`, then `link` when the message is saved).
The turn then reads the images back from storage and hands them to the
model with the text (`images_for`).

What an image is, is decided from its bytes: PNG, JPEG, WebP or GIF, by
their signatures, never by the type the browser claims. SVG is refused: it
is a document that can carry script, and its link is served from the
app's own origin.

The bytes live in object storage under
`attachments/<org>/<conversation>/<id>.<ext>`. The page reads them back
through the API (`read`), never from the store directly, so the bucket
needs no cross-origin rules. Erasing a conversation deletes them too
(`services/retention.purge`), and so does deleting its assistant. One
attached but never sent goes after a day (`sweep_unsent`).

Nothing here is audited, like the messages themselves: the audit log holds
no conversation content (Phase 7b.2).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.images import PromptImage
from app.api.errors import AppError, BadRequest, NotFound
from app.logging import get_logger
from app.models.attachment import Attachment
from app.models.conversation import Conversation, Message
from app.storage import delete_object, get_object, put_object

log = get_logger(__name__)

#: Largest image accepted. A full-screen screenshot is well under this.
MAX_BYTES = 5 * 1024 * 1024
#: Most images one message carries.
MAX_PER_MESSAGE = 4
#: How long the browser may keep a shown image before asking again.
CACHE_SECONDS = 300
#: How long an image attached but never sent is kept (Phase 9.6).
UNSENT_TTL = timedelta(hours=24)
#: Most unsent images one sweep removes; the next run takes the rest.
SWEEP_BATCH = 500

_TYPES = "PNG, JPEG, WebP or GIF"


def sniff(content: bytes) -> tuple[str, str] | None:
    """(media type, file extension) from an image's own signature, or None
    when the bytes are not one of the four kinds the model reads."""
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", "png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", "jpg"
    if content[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif", "gif"
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp", "webp"
    return None


async def upload(
    session: AsyncSession, conv: Conversation, *, user_id: uuid.UUID, content: bytes
) -> Attachment:
    """Keep one image in this conversation, not yet sent."""
    if len(content) > MAX_BYTES:
        raise BadRequest(
            f"This image is larger than {MAX_BYTES // (1024 * 1024)} MB. "
            "Crop it, or save it as a JPEG, and attach it again.",
            code="image_too_large",
        )
    found = sniff(content)
    if found is None:
        raise BadRequest(
            f"Only images can be attached: {_TYPES}. Take a screenshot and attach that.",
            code="unsupported_image_type",
        )
    media_type, ext = found
    row = Attachment(
        id=uuid.uuid4(),
        org_id=conv.org_id,
        conversation_id=conv.id,
        uploaded_by=user_id,
        content_type=media_type,
        bytes=len(content),
        object_key="",
    )
    row.object_key = f"attachments/{conv.org_id}/{conv.id}/{row.id}.{ext}"
    # Stored first: a row is never left pointing at nothing.
    await put_object(row.object_key, content, media_type)
    session.add(row)
    await session.commit()
    return row


async def get(session: AsyncSession, conv: Conversation, attachment_id: uuid.UUID) -> Attachment:
    """One of this conversation's attachments. Another conversation's is
    "not found", whoever asks."""
    row = await session.get(Attachment, attachment_id)
    if row is None or row.conversation_id != conv.id:
        raise NotFound("Attachment not found", code="attachment_not_found")
    return row


async def read(row: Attachment) -> bytes:
    """The image's bytes, for the page to show. Served by the API rather
    than a presigned storage link, so showing one needs no cross-origin
    rule on the bucket. A store that can't be read is a 503 to retry, not
    a "not found": the row says the image exists."""
    try:
        return await get_object(row.object_key)
    except Exception as exc:
        log.warning("attachment_read_failed", attachment_id=str(row.id), error=type(exc).__name__)
        raise AppError(
            "Couldn't read this screenshot from storage. Try again in a minute.",
            code="attachment_unreadable",
            status_code=503,
        ) from exc


def _unique(ids: Sequence[uuid.UUID]) -> list[uuid.UUID]:
    return list(dict.fromkeys(ids))


async def check_unsent(
    session: AsyncSession, conv: Conversation, ids: Sequence[uuid.UUID]
) -> list[uuid.UUID]:
    """Before a message is accepted: every attachment it names is this
    conversation's and not sent yet. Refused with a 400 otherwise, before
    the turn starts."""
    wanted = _unique(ids)
    if not wanted:
        return []
    if len(wanted) > MAX_PER_MESSAGE:
        raise BadRequest(
            f"A message can carry up to {MAX_PER_MESSAGE} screenshots. Remove some and send again.",
            code="too_many_attachments",
        )
    found = set(
        (
            await session.scalars(
                select(Attachment.id).where(
                    Attachment.id.in_(wanted),
                    Attachment.conversation_id == conv.id,
                    Attachment.message_id.is_(None),
                )
            )
        ).all()
    )
    if len(found) != len(wanted):
        raise BadRequest(
            "A screenshot on this message isn't in this conversation, or was already sent. "
            "Remove it, attach it again, and send.",
            code="attachment_unavailable",
        )
    return wanted


async def link(
    session: AsyncSession, conv_id: uuid.UUID, message: Message, ids: Sequence[uuid.UUID]
) -> list[Attachment]:
    """Mark these attachments as sent with `message` and add an image block
    for each to it, in the order they were attached. One already sent
    meanwhile is skipped. The caller commits."""
    wanted = _unique(ids)
    if not wanted:
        return []
    rows = (
        await session.scalars(
            select(Attachment).where(
                Attachment.id.in_(wanted),
                Attachment.conversation_id == conv_id,
                Attachment.message_id.is_(None),
            )
        )
    ).all()
    by_id = {r.id: r for r in rows}
    kept = [by_id[i] for i in wanted if i in by_id]
    for row in kept:
        row.message_id = message.id
    message.blocks = [
        *(message.blocks or []),
        *({"type": "image", "attachment_id": str(r.id)} for r in kept),
    ]
    return kept


async def images_for(rows: Sequence[Attachment]) -> tuple[list[PromptImage], int]:
    """The images to hand the model, read back from storage, and how many
    could not be read (the turn says so instead of pretending)."""
    images: list[PromptImage] = []
    missing = 0
    for row in rows:
        try:
            images.append(PromptImage(row.content_type, await get_object(row.object_key)))
        except Exception as exc:
            missing += 1
            log.warning(
                "attachment_read_failed", attachment_id=str(row.id), error=type(exc).__name__
            )
    return images, missing


async def keys_of(session: AsyncSession, conversation_ids: Sequence[uuid.UUID]) -> list[str]:
    """Where these conversations' images are, to delete with them."""
    if not conversation_ids:
        return []
    return list(
        (
            await session.scalars(
                select(Attachment.object_key).where(
                    Attachment.conversation_id.in_(list(conversation_ids))
                )
            )
        ).all()
    )


async def sweep_unsent(session: AsyncSession, *, now: datetime | None = None) -> int:
    """Delete images attached more than a day ago and never sent (Phase 9.6):
    the composer was closed, or the screenshot removed from it. Rows first,
    then their objects (best-effort). Only rows still unsent at the moment
    of the delete go, so one sent meanwhile is kept. Commits; returns how
    many went."""
    cutoff = (now or datetime.now(UTC)) - UNSENT_TTL
    ids = list(
        (
            await session.scalars(
                select(Attachment.id)
                .where(Attachment.message_id.is_(None), Attachment.created_at < cutoff)
                .limit(SWEEP_BATCH)
            )
        ).all()
    )
    if not ids:
        return 0
    keys = list(
        (
            await session.scalars(
                delete(Attachment)
                .where(Attachment.id.in_(ids), Attachment.message_id.is_(None))
                .returning(Attachment.object_key)
                .execution_options(synchronize_session=False)
            )
        ).all()
    )
    await session.commit()
    await delete_objects(keys)
    if keys:
        log.info("unsent_attachments_removed", count=len(keys))
    return len(keys)


async def delete_objects(keys: Sequence[str]) -> None:
    """Best-effort, after the rows are gone: a storage outage leaves an
    orphaned file (logged), never a row pointing at a deleted one."""
    for key in keys:
        try:
            await delete_object(key)
        except Exception as exc:
            log.warning("attachment_delete_failed", key=key, error=type(exc).__name__)


__all__ = [
    "MAX_BYTES",
    "MAX_PER_MESSAGE",
    "PromptImage",
    "check_unsent",
    "delete_objects",
    "get",
    "images_for",
    "keys_of",
    "link",
    "read",
    "sniff",
    "sweep_unsent",
    "upload",
]
