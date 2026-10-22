"""Where a message goes: an SMTP server, the log (development), a list in
the process (tests), or nowhere."""

from __future__ import annotations

import asyncio
import smtplib
import ssl

from app.config import settings
from app.logging import get_logger
from app.mail.message import Mail

log = get_logger(__name__)

#: Seconds an SMTP conversation may take before the attempt counts as failed.
SMTP_TIMEOUT_S = 20.0


class MailError(Exception):
    """Sending failed. `permanent`: retrying won't help (the server refused
    the address or the sign-in), so the message is recorded as failed now."""

    def __init__(self, message: str, *, permanent: bool = False) -> None:
        super().__init__(message)
        self.permanent = permanent


#: Messages the `memory` backend received, for tests.
outbox: list[Mail] = []


def _sender() -> tuple[str, str]:
    return settings.mail_from or settings.smtp_username, settings.mail_from_name


def _send_smtp(mail: Mail) -> None:
    sender, name = _sender()
    if not settings.smtp_host or not sender:
        raise MailError("SMTP_HOST and MAIL_FROM are not set", permanent=True)
    message = mail.to_email(sender=sender, sender_name=name)
    context = ssl.create_default_context()
    try:
        if settings.smtp_security == "ssl":
            client: smtplib.SMTP = smtplib.SMTP_SSL(
                settings.smtp_host, settings.smtp_port, timeout=SMTP_TIMEOUT_S, context=context
            )
        else:
            client = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=SMTP_TIMEOUT_S)
        with client:
            if settings.smtp_security == "starttls":
                client.starttls(context=context)
            if settings.smtp_username:
                client.login(settings.smtp_username, settings.smtp_password)
            client.send_message(message)
    except smtplib.SMTPAuthenticationError as exc:
        # The password is never in the message: only that it was refused.
        raise MailError("the SMTP server refused the sign-in", permanent=True) from exc
    except smtplib.SMTPRecipientsRefused as exc:
        raise MailError("the SMTP server refused the address", permanent=True) from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise MailError(f"could not send: {type(exc).__name__}") from exc


async def send(mail: Mail) -> None:
    """Deliver one message by the configured transport."""
    transport = settings.mail_transport
    if transport == "smtp":
        await asyncio.to_thread(_send_smtp, mail)
    elif transport == "console":
        # Development only (production defaults to `off`): the link is what
        # a developer needs to follow an invite.
        log.info("mail_console", to=mail.to, kind=mail.kind, subject=mail.subject, text=mail.text)
    elif transport == "memory":
        outbox.append(mail)
    else:
        log.info("mail_off", kind=mail.kind)


__all__ = ["SMTP_TIMEOUT_S", "MailError", "outbox", "send"]
