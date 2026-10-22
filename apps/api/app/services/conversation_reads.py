"""An admin reading someone else's conversation (Phase 7b.2, plan D5).

Conversations are private: each person sees their own. Admins and the owner
may read any, and every such read is recorded, with the reason they gave,
*before* the content is returned. Employees accept admins reading their
chats only because each read leaves a record; a read the record missed
breaks that promise.

- **One choke point.** Every route that returns a conversation's content
  goes through `require_conversation_reader` (`app.api.deps`), which calls
  `record` here; `tests/test_conversation_privacy.py` fails on a route that
  doesn't.
- **Committed first.** The request's own session commits only after the
  response is sent, so a row written there could be lost after the content
  was delivered. This commits before the handler runs, and a failure ends
  the request: no record, no content.
- **No noise.** Opening a conversation makes several calls (the thread, its
  messages, runs, the turn). One row per admin, conversation and reason in
  a 30-minute window.
- **No content in the record.** The reason, and who the reader was then
  (their role and email, kept even if the account goes later), never the
  conversation's text or title.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import unquote

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import BadRequest
from app.models.audit_log import AuditLog
from app.models.conversation import Conversation, ConversationStatus
from app.models.membership import Membership
from app.models.user import User
from app.services import audit

ACTION = "conversation.read_by_admin"
#: The header an admin's reason travels in: not the URL, which the proxy and
#: the access log record.
HEADER = "X-Read-Reason"
WINDOW = timedelta(minutes=30)
MAX_REASON = 500


def parse_reason(raw: str | None) -> str:
    """The reason as sent: URL-encoded by the web app (a header carries
    Latin-1 only), trimmed. Empty when none was given."""
    reason = unquote(raw or "").strip()
    if len(reason) > MAX_REASON:
        raise BadRequest(
            f"Keep the reason under {MAX_REASON} characters", code="read_reason_too_long"
        )
    return reason


async def record(
    session: AsyncSession,
    *,
    conversation: Conversation,
    reader: User,
    membership: Membership,
    reason: str,
    ip: str | None = None,
) -> bool:
    """Record the read and commit it. Returns False when the same admin
    gave the same reason for this conversation within the window."""
    since = datetime.now(UTC) - WINDOW
    recent = await session.scalars(
        select(AuditLog.meta).where(
            AuditLog.action == ACTION,
            AuditLog.actor_user_id == reader.id,
            AuditLog.target_id == str(conversation.id),
            AuditLog.created_at >= since,
        )
    )
    if any((meta or {}).get("reason") == reason for meta in recent):
        return False
    await audit.record(
        session,
        action=ACTION,
        org_id=conversation.org_id,
        actor_user_id=reader.id,
        target_type="conversation",
        target_id=conversation.id,
        meta={
            "reason": reason,
            "reader_email": reader.email,
            "reader_role": membership.role.value,
            # Whose conversation it was, by id: the person, not their words.
            "owner_id": str(conversation.created_by) if conversation.created_by else None,
            # Phase 8.8: its owner had removed it from their history.
            "hidden_by_owner": conversation.status is ConversationStatus.archived,
        },
        ip=ip,
    )
    await session.commit()
    return True


__all__ = ["ACTION", "HEADER", "MAX_REASON", "WINDOW", "parse_reason", "record"]
