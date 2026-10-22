"""What each message says. Plain text; links only into the app, to a page
where the thing is seen or decided while signed in; no conversation
content; every name through `clean_field`."""

from __future__ import annotations

from datetime import datetime

from app.config import settings
from app.mail.message import Mail, clean_field

FOOTER = (
    "\n\n--\n"
    "Open the link on the office network or VPN.\n"
    "Assistant Studio never asks you to approve anything, or to type your password, "
    "from an email. If a message asks you to, don't."
)


def app_link(path: str) -> str:
    return f"{settings.app_base_url.rstrip('/')}/{path.lstrip('/')}"


def invite(
    *, to: str, org_name: str, role: str, inviter: str, link: str, expires: datetime
) -> Mail:
    org, by = clean_field(org_name), clean_field(inviter)
    return Mail(
        to=to,
        kind="invite",
        subject=f"You're invited to {org} on Assistant Studio",
        text=(
            f"{by} invited you to join {org} on Assistant Studio as {role}.\n\n"
            f"Open this link to create your account or sign in:\n{link}\n\n"
            f"It works once, and until {expires:%d %b %Y}. If you weren't expecting it, "
            "ignore this email." + FOOTER
        ),
    )


def version_submitted(
    *, to: str, assistant_name: str, number: int, submitter: str, assistant_id: str
) -> Mail:
    name, by = clean_field(assistant_name), clean_field(submitter)
    return Mail(
        to=to,
        kind="version_submitted",
        subject=f"{name}: version {number} waits for your review",
        text=(
            f"{by} submitted version {number} of {name} for review. Nobody can chat with it "
            "until an admin approves it.\n\n"
            f"Review it in Assistant Studio (History):\n"
            f"{app_link(f'/assistants/{assistant_id}/build?tab=history')}" + FOOTER
        ),
    )


def approval_waiting(
    *,
    to: str,
    asker: str,
    assistant_name: str,
    what: str,
    reference: str,
    expires: datetime | None,
    approval_id: str,
    reminder: bool = False,
) -> Mail:
    """A request waits for an approver (Phase 9.1). The link opens its page
    (Phase 9.2), where it is read and decided while signed in; nothing in
    the email decides anything, and what it would do isn't in it."""
    who, name, thing = clean_field(asker), clean_field(assistant_name), clean_field(what)
    until = f" It waits until {expires:%d %b %Y, %H:%M} UTC." if expires else ""
    return Mail(
        to=to,
        kind="approval_reminder" if reminder else "approval_waiting",
        subject=(
            f"Reminder: {reference} from {who} still waits"
            if reminder
            else f"{name}: {who} asks for {thing} ({reference})"
        ),
        text=(
            f"{who} asked {name} for {thing} ({reference}). You're one of the people who "
            f"decide it.{until} Nothing happens until someone does.\n\n"
            f"Open it in Assistant Studio to decide:\n{app_link(f'/approvals/{approval_id}')}"
            + FOOTER
        ),
    )


def approvals_waiting(*, to: str, count: int) -> Mail:
    """Several requests wait (Phase 9.2): one email for a burst of them."""
    what = "1 request waits" if count == 1 else f"{count} requests wait"
    return Mail(
        to=to,
        kind="approvals_waiting",
        subject=f"{what} for you to decide",
        text=(
            f"{what} for you to decide in Assistant Studio. Nothing happens until "
            "someone does.\n\n"
            f"See them in Approvals:\n{app_link('/approvals')}" + FOOTER
        ),
    )


def approval_outcome(
    *,
    to: str,
    assistant_name: str,
    what: str,
    reference: str,
    status: str,
    approval_id: str,
) -> Mail:
    """How someone's request ended (Phase 9.1). The details are in the
    conversation, behind sign-in."""
    name, thing = clean_field(assistant_name), clean_field(what)
    said = {
        "succeeded": "was approved and done",
        "failed": "was approved, but didn't go through",
        "denied": "was declined",
        "expired": "wasn't decided in time, so nothing was done",
    }.get(status, "has ended")
    link = app_link(f"/approvals/{approval_id}")
    return Mail(
        to=to,
        kind="approval_outcome",
        subject=f"{reference}: your request {said}",
        text=(
            f"Your request to {name} for {thing} ({reference}) {said}.\n\n"
            f"See where it stands:\n{link}" + FOOTER
        ),
    )


def version_reviewed(
    *, to: str, assistant_name: str, number: int, approved: bool, assistant_id: str
) -> Mail:
    name = clean_field(assistant_name)
    outcome = "approved" if approved else "rejected"
    then = (
        "It now answers in Chat. Conversations already going stay on the version they started on."
        if approved
        else "The reviewer's reason is in History."
    )
    return Mail(
        to=to,
        kind="version_reviewed",
        subject=f"{name}: version {number} was {outcome}",
        text=(
            f"Version {number} of {name} was {outcome}. {then}\n\n"
            f"{app_link(f'/assistants/{assistant_id}/build?tab=history')}" + FOOTER
        ),
    )


def reported_fixed(*, to: str, assistant_name: str, number: int, assistant_id: str) -> Mail:
    """Something this person reported in Chat was fixed (Phase 10.3). What
    they asked, and what it answered, stay in the app."""
    name = clean_field(assistant_name)
    return Mail(
        to=to,
        kind="reported_fixed",
        subject=f"{name}: something you reported was fixed",
        text=(
            f"Something you reported to {name} was fixed in version {number}. "
            "Open Chat to try again: a new conversation uses the fixed version.\n\n"
            f"{app_link(f'/assistants/{assistant_id}/chat')}" + FOOTER
        ),
    )


def handoff(*, to: str, asker: str, assistant_name: str, link: str) -> Mail:
    """Someone asked for a person's help (Phase 9.5). Who and which
    assistant only: what was said, the summary and their note stay in the
    app, behind sign-in, where an admin's read is recorded."""
    who, name = clean_field(asker), clean_field(assistant_name)
    return Mail(
        to=to,
        kind="handoff",
        subject=f"{who} asked for help with {name}",
        text=(
            f"{who} asked for a person's help in a conversation with {name}. You're the "
            "contact its builder named for this.\n\n"
            "What was said stays in Assistant Studio. Open the conversation there:\n"
            f"{link}\n\n"
            "If it isn't your own conversation, you open it as an admin: you're asked why, "
            "and your reading is recorded." + FOOTER
        ),
    )


def admin_alert(*, to: str, what: str, detail: str) -> Mail:
    """Chat has stopped for everyone (Phase 8.9). At most once an hour for
    each kind of problem."""
    more = f"\n\nWhat the server said: {clean_field(detail)}" if detail else ""
    return Mail(
        to=to,
        kind="admin_alert",
        subject=f"Assistant Studio: {clean_field(what)}",
        text=(
            f"{clean_field(what)}. Chat may not be answering anyone until it is fixed.{more}\n\n"
            "Mark it handled once it's fixed, or the next answer that works clears it. "
            f"See the alerts in Assistant Studio:\n{app_link('/alerts')}" + FOOTER
        ),
    )


def password_reset(*, to: str, link: str, minutes: int) -> Mail:
    """The link opens a page where a new password is typed: opening it
    changes nothing, so a mail scanner following it does no harm."""
    return Mail(
        to=to,
        kind="password_reset",
        subject="Reset your Assistant Studio password",
        text=(
            "Someone asked to reset the password for this address on Assistant Studio.\n\n"
            f"To choose a new one, open this link within {minutes} minutes:\n{link}\n\n"
            "It works once. If you didn't ask, ignore this email: your password stays as it is."
            + FOOTER
        ),
    )


def password_changed(*, to: str) -> Mail:
    return Mail(
        to=to,
        kind="password_changed",
        subject="Your Assistant Studio password was changed",
        text=(
            "The password for this address on Assistant Studio was just changed, and every "
            "session was signed out.\n\n"
            "If that was you, there is nothing to do. If it wasn't, reset it now and tell an "
            f"admin:\n{app_link('/forgot-password')}" + FOOTER
        ),
    )


def verify_email(*, to: str, link: str, hours: int) -> Mail:
    return Mail(
        to=to,
        kind="verify_email",
        subject="Confirm your email for Assistant Studio",
        text=(
            "Confirm that this address is yours by opening this link within "
            f"{hours} hours:\n{link}\n\nIf you didn't sign up, ignore this email." + FOOTER
        ),
    )


def test(*, to: str) -> Mail:
    return Mail(
        to=to,
        kind="test",
        subject="Assistant Studio: test email",
        text=(
            "This is a test from Assistant Studio. Email from this server reaches you.\n\n"
            f"{app_link('/')}" + FOOTER
        ),
    )


__all__ = [
    "FOOTER",
    "admin_alert",
    "app_link",
    "approval_waiting",
    "handoff",
    "invite",
    "password_changed",
    "password_reset",
    "test",
    "verify_email",
    "version_reviewed",
    "version_submitted",
]
