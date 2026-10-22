"""Sending email: record it, queue it, deliver it with retries (Phase 7b.5).

`send` records the message (`mail_messages`: who to, what kind, the
subject, never the body) and puts it on the mail queue, which its own
worker drains (`app.worker.MailWorkerSettings`): a slow SMTP server never
holds up a request, and mail never waits behind an hour of indexing. The
body travels only in the queued job.

Delivery retries a failure that may pass (a timeout, a server busy) with a
growing wait, up to `MAIL_MAX_TRIES`, and stops at once on one that won't
(the sign-in or the address refused). The `console` and `memory`
transports (development and tests) deliver straight away, with no queue.

Nothing here fails the request that asked for a message: an invite is
still created if its email can't be queued, and the admin still gets the
link to send by hand. The record says what happened.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.config import settings
from app.db.session import get_sessionmaker
from app.logging import get_logger
from app.mail import backends
from app.mail.message import Mail
from app.models.mail import MailMessage, MailStatus

log = get_logger(__name__)

#: The Arq queue mail goes on, apart from the main worker's.
MAIL_QUEUE = "arq:mail"
#: The first retry waits this long; each later one twice as long, up to MAX.
RETRY_BASE_S = 30
RETRY_MAX_S = 1800


def retry_delay_s(attempt: int) -> int:
    return int(min(RETRY_MAX_S, RETRY_BASE_S * 2 ** max(0, attempt - 1)))


async def send(mail: Mail, *, org_id: uuid.UUID | None) -> MailMessage | None:
    """Record and queue one message. None when mail is off."""
    transport = settings.mail_transport
    if transport == "off":
        log.info("mail_off", kind=mail.kind)
        return None
    async with get_sessionmaker()() as session:
        row = MailMessage(
            org_id=org_id, to_address=mail.to, kind=mail.kind, subject=mail.subject[:300]
        )
        session.add(row)
        await session.commit()
        message_id = row.id
    if transport in ("console", "memory"):
        await deliver(message_id, mail.as_dict(), attempt=1)
        return row
    from app.queue import enqueue_mail

    if not await enqueue_mail(message_id, mail.as_dict()):
        await _record(
            message_id, MailStatus.failed, attempts=0, error="the mail queue was unavailable"
        )
    return row


async def deliver(message_id: uuid.UUID, data: dict, *, attempt: int) -> bool:
    """Try once. True when sent or given up for good; False when it should
    be retried (the job raises `Retry` for that)."""
    mail = Mail.from_dict(data)
    try:
        await backends.send(mail)
    except backends.MailError as exc:
        final = exc.permanent or attempt >= settings.mail_max_tries
        await _record(
            message_id,
            MailStatus.failed if final else MailStatus.queued,
            attempts=attempt,
            error=str(exc)[:300],
        )
        log.warning(
            "mail_failed", kind=mail.kind, attempt=attempt, final=final, error=str(exc)[:200]
        )
        return final
    await _record(message_id, MailStatus.sent, attempts=attempt, error=None)
    return True


async def _record(
    message_id: uuid.UUID, status: MailStatus, *, attempts: int, error: str | None
) -> None:
    async with get_sessionmaker()() as session:
        row = await session.get(MailMessage, message_id)
        if row is None:
            return
        row.status, row.attempts, row.last_error = status, attempts, error
        if status is MailStatus.sent:
            row.sent_at = datetime.now(UTC)
        await session.commit()


__all__ = ["MAIL_QUEUE", "deliver", "retry_delay_s", "send"]
