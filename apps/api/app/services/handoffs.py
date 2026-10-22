"""Hand-offs to a person (Phase 9.5, plan task 9.5).

When the assistant can't help, the person gets a person: "Get help from a
person" above the chat, or the assistant's own `hand_off` tool. Either way:

- **Only where it's on.** The assistant's `handoff` settings (on the agent
  node, approved with the version) say who is told. A preview is never
  handed off (`preview_no_handoff`); from the assistant's tool in a preview
  it only says what would happen, so a builder can test it.
- **A summary without a model call.** The conversation's title, the
  person's last few questions, the last answer and the note, each cut short
  and with secrets stripped (`strip_secrets`); URLs are taken out of the
  note. It stays here, behind sign-in.
- **The email carries no conversation content** (the rule of `app.mail`):
  who asked, which assistant, and a link to the conversation in the app,
  where an admin's read is recorded.
- **Or a ticket.** With `handoff.action` naming one of the HTTP request
  tool's declared actions (9.3), that call is made as the person instead
  of the email, its `title`, `summary` and `note` parameters filled in
  when it declares them. A `read` action runs from either side; a
  `confirm` one only from the person's own click, which is their
  confirmation, so from the assistant's tool it falls back to the email.
  An `approver` action is refused when the version is checked
  (`graph_refs`); should one slip through, the email goes instead.
- **Once in a while.** One hand-off that went through per conversation per
  `WINDOW` (`handoff_recent`): the contact has it already.

Each is a `handoffs` row and a `conversation.handoff` audit entry.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.actions import ActionInputError, Asker
from app.agent.caps_handoff import NOTE_MAX, HandOff, HandoffRefused
from app.api.errors import AppError, BadRequest, Conflict
from app.logging import get_logger
from app.mail import templates
from app.models.assistant import Assistant
from app.models.conversation import Conversation, ConversationMode, Message, MessageRole
from app.models.handoff import TICKET_REF_MAX, Handoff, HandoffChannel, HandoffStatus
from app.models.user import User
from app.schemas.assistant_config import AssistantConfig, HttpAction
from app.schemas.handoff import HandoffList, HandoffOut
from app.security.redact import mask_typed_secrets, strip_secrets
from app.services import audit, mail

log = get_logger(__name__)

#: One hand-off that went through per conversation in this long.
WINDOW = timedelta(minutes=10)
#: What the summary keeps.
QUESTIONS = 3
QUESTION_CHARS = 300
ANSWER_CHARS = 800
SUMMARY_CHARS = 4000
#: The values a hand-off fills in a ticket action (`graph_refs.HANDOFF_PARAMS`).
TICKET_PARAMS = ("title", "summary", "note")

_URL = re.compile(r"(?i)\b(?:https?://|www\.|mailto:)\S*")
_SPACE = re.compile(r"[ \t]+")


def _cut(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def clean_note(note: str) -> str:
    """The note as it is kept: no secrets, no web addresses, not too long."""
    # Masked as a chat message is (Phase 9.9): "my password is …" too.
    text = _URL.sub("[link removed]", mask_typed_secrets(note or "")[0])
    return _cut(_SPACE.sub(" ", text), NOTE_MAX)


def summarize(
    *,
    title: str,
    assistant_name: str,
    asker: str,
    note: str,
    by_assistant: bool,
    rows: list[Message],
) -> str:
    """What the contact reads first, from the conversation itself."""
    questions = [m.content for m in rows if m.role is MessageRole.user and m.content.strip()]
    answers = [m.content for m in rows if m.role is MessageRole.assistant and m.content.strip()]
    lines = [
        f"Conversation: {_cut(title, 200)}",
        f"Assistant: {assistant_name}",
        f"Asked by: {asker}",
    ]
    if note:
        who = "The assistant's note" if by_assistant else "Their note"
        lines.append(f"{who}: {note}")
    if questions:
        lines.append("")
        lines.append("What they asked, most recent last:")
        lines += [f"- {_cut(q, QUESTION_CHARS)}" for q in questions[-QUESTIONS:]]
    if answers:
        lines.append("")
        lines.append("The assistant's last answer:")
        lines.append(_cut(answers[-1], ANSWER_CHARS))
    return _cut(strip_secrets("\n".join(lines)), SUMMARY_CHARS)


def link(conversation: Conversation) -> str:
    """The conversation in the app, where it is read while signed in."""
    return templates.app_link(f"/assistants/{conversation.assistant_id}/chat?c={conversation.id}")


def _is_preview(conversation: Conversation) -> bool:
    return conversation.mode is ConversationMode.preview or conversation.eval_run_id is not None


def ticket_action(config: AssistantConfig, *, by_assistant: bool) -> HttpAction | None:
    """The HTTP action a hand-off opens a ticket with, when it may run
    from this side: `read` from either, `confirm` from the person only."""
    name = config.handoff.action
    tool = config.tools.http_request
    if not name or not tool.enabled:
        return None
    action = next((a for a in tool.actions if a.name == name), None)
    if action is None or action.tier == "approver":
        # Refused when the version is checked; this is drift. Email instead.
        log.warning("handoff_action_unusable", action=name)
        return None
    if action.tier == "confirm" and by_assistant:
        return None
    return action


async def _config(session: AsyncSession, conversation: Conversation) -> AssistantConfig:
    from app.services import chat

    # Reading settings, not answering: a paused assistant still lets people
    # get help.
    return await chat.config_for(session, conversation, answering=False)


async def _recent(session: AsyncSession, conversation_id: uuid.UUID) -> bool:
    since = datetime.now(UTC) - WINDOW
    row = await session.scalar(
        select(Handoff.id)
        .where(
            Handoff.conversation_id == conversation_id,
            Handoff.status == HandoffStatus.sent,
            Handoff.created_at >= since,
        )
        .limit(1)
    )
    return row is not None


async def _messages(session: AsyncSession, conversation_id: uuid.UUID) -> list[Message]:
    rows = await session.scalars(
        select(Message)
        .where(
            Message.conversation_id == conversation_id,
            Message.role.in_((MessageRole.user, MessageRole.assistant)),
        )
        .order_by(Message.created_at)
    )
    return list(rows.all())


def _ticket_args(action: HttpAction, values: dict[str, str]) -> dict[str, Any]:
    """The hand-off's values for the parameters the action declares, each
    within its length."""
    out: dict[str, Any] = {}
    for param in action.params:
        value = values.get(param.name)
        if param.name in TICKET_PARAMS and value:
            out[param.name] = _cut(value, param.max_length)
    return out


async def _open_ticket(
    conversation: Conversation, action: HttpAction, asker: Asker, values: dict[str, str]
) -> tuple[bool, str | None]:
    """Make the call as the person: whether it went through, and the start
    of the system's answer (or why not)."""
    from app.agent import http_actions

    try:
        call, _ = http_actions.render(action, _ticket_args(action, values), asker=asker)
    except ActionInputError as exc:
        return False, _cut(f"The ticket action can't be filled in: {exc}", TICKET_REF_MAX)
    result = await http_actions.send(conversation.assistant_id, action, call, asker)
    text = "".join(str(c.get("text", "")) for c in result.get("content", []))
    # The answer's first line is its status; what follows is the body.
    head, _, body = text.partition("\n\n")
    return not result.get("is_error"), _cut(strip_secrets(body or head), TICKET_REF_MAX) or None


async def request(
    session: AsyncSession,
    conversation: Conversation,
    *,
    user: User,
    note: str = "",
    by_assistant: bool = False,
    ip: str | None = None,
) -> Handoff:
    """Hand the conversation to a person, as `user` (its starter). Raises
    an AppError the caller can show when it can't be."""
    if _is_preview(conversation):
        raise BadRequest(
            "A preview isn't handed to anyone: in Chat, this would reach the contact",
            code="preview_no_handoff",
        )
    config = await _config(session, conversation)
    settings_ = config.handoff
    if not settings_.enabled:
        raise BadRequest(
            "This assistant doesn't hand conversations to a person. Ask its builder to turn "
            "on Hand-off in its settings",
            code="handoff_off",
        )
    # One at a time per conversation (Phase 9.9): two clicks at the same
    # instant both passed the check below. On Postgres the second waits for
    # the first to finish, then sees it; SQLite has one writer anyway.
    await session.execute(
        select(Conversation.id).where(Conversation.id == conversation.id).with_for_update()
    )
    if await _recent(session, conversation.id):
        raise Conflict(
            "This conversation was handed to a person in the last "
            f"{int(WINDOW.total_seconds() // 60)} minutes. They have it already",
            code="handoff_recent",
        )
    assistant = await session.get(Assistant, conversation.assistant_id)
    assistant_name = assistant.name if assistant else "An assistant"
    asker_name = user.name or user.email
    cleaned = clean_note(note)
    summary = summarize(
        title=conversation.title,
        assistant_name=assistant_name,
        asker=asker_name,
        note=cleaned,
        by_assistant=by_assistant,
        rows=await _messages(session, conversation.id),
    )

    action = ticket_action(config, by_assistant=by_assistant)
    ticket_ref: str | None = None
    if action is not None:
        channel = HandoffChannel.action
        ok, ticket_ref = await _open_ticket(
            conversation,
            action,
            Asker(user_id=user.id, email=user.email, name=asker_name),
            {
                "title": f"{conversation.title} ({assistant_name})",
                "summary": summary,
                "note": cleaned,
            },
        )
    else:
        channel = HandoffChannel.email
        sent = await mail.send(
            templates.handoff(
                to=settings_.contact_email,
                asker=asker_name,
                assistant_name=assistant_name,
                link=link(conversation),
            ),
            org_id=conversation.org_id,
        )
        ok = sent is not None

    row = Handoff(
        org_id=conversation.org_id,
        conversation_id=conversation.id,
        assistant_id=conversation.assistant_id,
        requested_by=user.id,
        summary=summary,
        status=HandoffStatus.sent if ok else HandoffStatus.failed,
        channel=channel,
        ticket_ref=ticket_ref,
    )
    session.add(row)
    await session.flush()
    await audit.record(
        session,
        action="conversation.handoff",
        org_id=conversation.org_id,
        actor_user_id=user.id,
        target_type="conversation",
        target_id=conversation.id,
        meta={
            "handoff_id": str(row.id),
            "channel": channel.value,
            "status": row.status.value,
            "by": "assistant" if by_assistant else "person",
            **({"action": action.name} if action is not None else {}),
        },
        ip=ip,
    )
    await session.commit()
    await session.refresh(row)
    return row


async def list_for(session: AsyncSession, conversation: Conversation) -> HandoffList:
    rows = await session.scalars(
        select(Handoff)
        .where(Handoff.conversation_id == conversation.id)
        .order_by(Handoff.created_at.desc())
    )
    items = [HandoffOut.model_validate(r) for r in rows.all()]
    if _is_preview(conversation):
        return HandoffList(available=False, contact_email="", opens_ticket=False, items=items)
    try:
        config = await _config(session, conversation)
    except AppError:
        config = None
    if config is None or not config.handoff.enabled:
        return HandoffList(available=False, contact_email="", opens_ticket=False, items=items)
    return HandoffList(
        available=True,
        contact_email=config.handoff.contact_email,
        opens_ticket=ticket_action(config, by_assistant=False) is not None,
        items=items,
    )


# ── the assistant's own `hand_off` tool ──────────────────────


def _said(row: Handoff, contact: str) -> str:
    """What the model is told happened, to pass on."""
    if row.channel is HandoffChannel.action:
        ref = f" The system answered: {row.ticket_ref}" if row.ticket_ref else ""
        return f"A ticket was opened for the person, with a summary of this conversation.{ref}"
    return (
        f"Handed to {contact}: they were emailed a link to this conversation and will open "
        "it in the app."
    )


async def _from_assistant(conversation_id: uuid.UUID, user_id: uuid.UUID, note: str) -> str:
    from app.db.session import get_sessionmaker

    async with get_sessionmaker()() as session:
        conversation = await session.get(Conversation, conversation_id)
        user = await session.get(User, user_id)
        if conversation is None or user is None:
            raise HandoffRefused("this conversation can't be handed off")
        if _is_preview(conversation):
            config = await _config(session, conversation)
            return (
                "This is a preview, so nobody was told. In Chat, "
                f"{config.handoff.contact_email or 'the hand-off contact'} would get this "
                "conversation."
            )
        try:
            row = await request(session, conversation, user=user, note=note, by_assistant=True)
        except AppError as exc:
            raise HandoffRefused(exc.message) from exc
        if row.status is HandoffStatus.failed:
            raise HandoffRefused(
                "the hand-off didn't go through. Tell the person to try 'Get help from a "
                "person' above the chat in a little while"
            )
        config = await _config(session, conversation)
        return _said(row, config.handoff.contact_email)


def hook(conversation: Conversation, asker: Asker | None, *, unattended: bool) -> HandOff | None:
    """The turn's `hand_off` work, bound to this conversation; None when it
    has nobody to hand off for (an eval, or no signed-in person)."""
    if unattended or asker is None or conversation.created_by != asker.user_id:
        return None
    conversation_id, user_id = conversation.id, asker.user_id

    async def hand_off(note: str) -> str:
        return await _from_assistant(conversation_id, user_id, note)

    return hand_off


__all__ = [
    "WINDOW",
    "clean_note",
    "hook",
    "link",
    "list_for",
    "request",
    "summarize",
    "ticket_action",
]
