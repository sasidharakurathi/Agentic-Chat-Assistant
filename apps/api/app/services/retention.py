"""One way conversations are erased for good (Phase 8.10).

Removing a conversation from your history (8.8) only hides it. Erasing is
this module, and nothing else: the retention rule every night, and the
owner erasing one person. Both go through `purge`, so both reach every
place a conversation's content lives:

- **the database**: deleting the conversation row takes its messages, runs,
  tool calls and their results, approvals and the feedback on it (ON DELETE
  CASCADE). The spend ledger and "Did this solve it?" stay, without it (SET
  NULL), so totals don't change;
- **memory notes** kept for that conversation (`conv:<id>`; no foreign key,
  so deleted here);
- **the CLI's transcript** of it on disk, and its session key in Redis;
- **screenshots** sent in it, in object storage (Phase 9.6).

Each purge counts what went with the conversations before they go (Phase
10.5: messages, run traces and tool calls, summaries, share links, feedback,
review decisions, the conversation's memory notes, screenshots) into the
erasure's `details`, so "What was erased" says so. A person's own memory
notes (`user:<id>`) aren't tied to a conversation, and retention is about
quiet conversations: they go only when that person is erased.

What it skips: a conversation, or a person, under a **legal hold**, and a
conversation answering right now. Each purge is recorded in `erasures`
(ids only) so `scripts/reapply_erasures.py` can erase the same again after
a backup is restored; backups themselves age out on their own rotation.

The audit log stays: it holds no conversation content (7b.2), and who did
what is what it is for.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import cli_files, session_store
from app.api.errors import BadRequest, Conflict, NotFound
from app.logging import get_logger
from app.models.assistant import Assistant
from app.models.conversation import Conversation, ConversationShare, Message, Run, ToolCall
from app.models.feedback import MessageFeedback
from app.models.membership import Membership
from app.models.memory import MemoryFile
from app.models.organization import Organization
from app.models.retention import Erasure, LegalHold
from app.models.review import ReviewItem
from app.models.usage import UsageEvent
from app.models.user import User
from app.services import attachments, audit, org_settings, turns

log = get_logger(__name__)

#: Conversations erased per transaction: small enough that people chatting
#: are never kept waiting on a lock.
BATCH = 500
#: At most this many batches a night per organisation; the rest wait.
MAX_BATCHES = 20
#: The retention setting's bounds, in days. Below a year, the page warns
#: about India's DPDP one-year floor for logs; the company decides.
DAYS_MIN, DAYS_MAX, DPDP_FLOOR = 30, 3650, 365


async def _held(session: AsyncSession, org_id: uuid.UUID) -> tuple[set[uuid.UUID], set[uuid.UUID]]:
    """The people and conversations under a hold."""
    rows = (
        await session.execute(
            select(LegalHold.user_id, LegalHold.conversation_id).where(
                LegalHold.org_id == org_id, LegalHold.released_at.is_(None)
            )
        )
    ).all()
    return {u for u, _ in rows if u}, {c for _, c in rows if c}


async def _count(session: AsyncSession, stmt: Any) -> int:
    return int(await session.scalar(stmt) or 0)


async def _tally_of(session: AsyncSession, gone: list[uuid.UUID], tally: dict[str, int]) -> None:
    """Add what goes with these conversations to `tally` (Phase 10.5):
    counted before the delete, since the cascade leaves nothing to count."""
    n = func.count()
    counts = {
        "messages": select(n).select_from(Message).where(Message.conversation_id.in_(gone)),
        "traces": select(n).select_from(Run).where(Run.conversation_id.in_(gone)),
        "tool_calls": select(n).select_from(ToolCall).where(ToolCall.conversation_id.in_(gone)),
        "summaries": select(n)
        .select_from(Conversation)
        .where(Conversation.id.in_(gone), Conversation.summary.is_not(None)),
        "share_links": select(n)
        .select_from(ConversationShare)
        .where(ConversationShare.conversation_id.in_(gone)),
        "feedback": select(n)
        .select_from(MessageFeedback)
        .where(MessageFeedback.conversation_id.in_(gone)),
        "review_items": select(n)
        .select_from(ReviewItem)
        .join(Message, Message.id == ReviewItem.message_id)
        .where(Message.conversation_id.in_(gone)),
        "conversation_memories": select(n)
        .select_from(MemoryFile)
        .where(MemoryFile.owner_key.in_([f"conv:{c}" for c in gone])),
    }
    for key, stmt in counts.items():
        tally[key] = tally.get(key, 0) + await _count(session, stmt)


async def purge(
    session: AsyncSession,
    org_id: uuid.UUID,
    ids: Sequence[uuid.UUID],
    *,
    tally: dict[str, int] | None = None,
) -> list[uuid.UUID]:
    """Erase these conversations for good, everywhere they live. Returns the
    ones erased: not those under a hold, nor one answering right now.
    `tally`, when given, gets what went with them added to it."""
    if not ids:
        return []
    people, conversations = await _held(session, org_id)
    rows = (
        await session.execute(
            select(Conversation.id, Conversation.created_by).where(
                Conversation.org_id == org_id, Conversation.id.in_(list(ids))
            )
        )
    ).all()
    # A request still waiting or running keeps its conversation (Phase 9.1):
    # the outcome is posted there.
    from app.models.approval import OPEN, Approval

    waiting = set(
        (
            await session.scalars(
                select(Approval.conversation_id).where(
                    Approval.conversation_id.in_(list(ids)), Approval.status.in_(OPEN)
                )
            )
        ).all()
    )
    gone: list[uuid.UUID] = []
    for cid, owner in rows:
        if cid in conversations or cid in waiting or (owner is not None and owner in people):
            continue
        if await turns.running_turn(cid):
            continue
        gone.append(cid)
    if not gone:
        return []
    # Screenshots sent in them (Phase 9.6): the rows go with the
    # conversation (CASCADE), the images in storage after the commit.
    image_keys = await attachments.keys_of(session, gone)
    if tally is not None:
        await _tally_of(session, gone, tally)
        tally["screenshots"] = tally.get("screenshots", 0) + len(image_keys)
    await session.execute(
        delete(MemoryFile)
        .where(MemoryFile.owner_key.in_([f"conv:{c}" for c in gone]))
        .execution_options(synchronize_session=False)
    )
    await session.execute(
        delete(Conversation)
        .where(Conversation.id.in_(gone))
        .execution_options(synchronize_session=False)
    )
    await session.commit()
    for cid in gone:
        # Off the database: a failure here leaves a file the nightly sweep
        # (7a.5) removes anyway.
        try:
            await session_store.delete(cid)
        except Exception:
            log.warning("purge_session_key_failed", conversation_id=str(cid))
        cli_files.discard(cid)
    await attachments.delete_objects(image_keys)
    return gone


# ── the retention rule ───────────────────────────────────────


async def apply_retention(
    session: AsyncSession, org_id: uuid.UUID, *, now: datetime | None = None
) -> int:
    """Erase the org's conversations quiet for longer than its retention
    setting, in batches. Returns how many."""
    days = (await org_settings.switches(session, org_id)).retention_days
    if not days:
        return 0
    cutoff = (now or datetime.now(UTC)) - timedelta(days=days)
    total = 0
    for _ in range(MAX_BATCHES):
        stale = list(
            (
                await session.scalars(
                    select(Conversation.id)
                    .where(
                        Conversation.org_id == org_id,
                        func.coalesce(Conversation.last_message_at, Conversation.created_at)
                        < cutoff,
                    )
                    .order_by(Conversation.created_at)
                    .limit(BATCH)
                )
            ).all()
        )
        if not stale:
            break
        details: dict[str, int] = {}
        gone = await purge(session, org_id, stale, tally=details)
        if gone:
            session.add(
                Erasure(
                    org_id=org_id,
                    kind="retention",
                    cutoff=cutoff,
                    conversation_ids=[str(c) for c in gone],
                    details=details,
                )
            )
            await session.commit()
            total += len(gone)
        if len(gone) < len(stale):
            # The rest are held or answering: they stay, and so would the
            # same batch next time round. Done for tonight.
            break
    return total


async def retention_sweep(now: datetime | None = None) -> int:
    """Every organisation's retention, nightly (the worker's cron)."""
    from app.db.session import get_sessionmaker
    from app.db.tenancy import bind_org

    total = 0
    async with get_sessionmaker()() as session:
        org_ids = list(await session.scalars(select(Organization.id)))
    for org_id in org_ids:
        try:
            async with get_sessionmaker()() as session:
                bind_org(session, org_id)
                total += await apply_retention(session, org_id, now=now)
        except Exception:
            log.exception("retention_failed", org_id=str(org_id))
    if total:
        log.info("retention_erased", conversations=total)
    return total


# ── erasing one person ───────────────────────────────────────


def _their_memories(org_id: uuid.UUID, user_id: uuid.UUID):  # type: ignore[no-untyped-def]
    """What this org's assistants remember about one person (`user:<id>`,
    no foreign key, so nothing else removes it)."""
    return (
        delete(MemoryFile)
        .where(
            MemoryFile.owner_key == f"user:{user_id}",
            MemoryFile.assistant_id.in_(select(Assistant.id).where(Assistant.org_id == org_id)),
        )
        .execution_options(synchronize_session=False)
    )


@dataclass(frozen=True)
class Erased:
    conversations: int
    kept: int
    memories: int
    comments: int
    #: What went with the conversations (Phase 10.5): see `_tally_of`.
    details: dict[str, int] = field(default_factory=dict)


async def erase_person(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    confirm_email: str,
    actor_id: uuid.UUID,
    ip: str | None,
) -> Erased:
    """The owner erases one person's data in the org: their conversations,
    what assistants remember about them, their feedback comments. Their
    account stays, and so does the audit log; their spend stays in the
    totals, without their name. `confirm_email` must be theirs."""
    user = await session.scalar(
        select(User)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.org_id == org_id, User.id == user_id)
    )
    if user is None:
        raise NotFound("Member not found")
    if confirm_email.strip().lower() != user.email.lower():
        raise BadRequest(
            "Type their email address exactly to confirm", code="confirm_email_mismatch"
        )
    people, _ = await _held(session, org_id)
    if user_id in people:
        raise Conflict(
            "This person is under a legal hold. Lift it first, if it may be lifted",
            code="on_legal_hold",
        )
    theirs = list(
        (
            await session.scalars(
                select(Conversation.id).where(
                    Conversation.org_id == org_id, Conversation.created_by == user_id
                )
            )
        ).all()
    )
    gone: list[uuid.UUID] = []
    details: dict[str, int] = {}
    for start in range(0, len(theirs), BATCH):
        gone += await purge(session, org_id, theirs[start : start + BATCH], tally=details)
    memories = await session.execute(_their_memories(org_id, user_id))
    comments = await session.execute(
        delete(MessageFeedback)
        .where(MessageFeedback.org_id == org_id, MessageFeedback.user_id == user_id)
        .execution_options(synchronize_session=False)
    )
    await session.execute(
        update(UsageEvent)
        .where(UsageEvent.org_id == org_id, UsageEvent.user_id == user_id)
        .values(user_id=None)
        .execution_options(synchronize_session=False)
    )
    n_memories = int(memories.rowcount or 0)  # type: ignore[attr-defined]
    n_comments = int(comments.rowcount or 0)  # type: ignore[attr-defined]
    session.add(
        Erasure(
            org_id=org_id,
            kind="person",
            user_id=user_id,
            conversation_ids=[str(c) for c in gone],
            memories=n_memories,
            comments=n_comments,
            details=details,
            created_by=actor_id,
        )
    )
    await audit.record(
        session,
        action="person.erase",
        org_id=org_id,
        actor_user_id=actor_id,
        target_type="user",
        target_id=user_id,
        meta={
            "conversations": len(gone),
            "kept": len(theirs) - len(gone),
            "memories": n_memories,
            "comments": n_comments,
            "details": details,
        },
        ip=ip,
    )
    await session.commit()
    return Erased(len(gone), len(theirs) - len(gone), n_memories, n_comments, details)


# ── legal holds ──────────────────────────────────────────────


async def hold(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID | None,
    conversation_id: uuid.UUID | None,
    reason: str,
    actor_id: uuid.UUID,
    ip: str | None,
) -> LegalHold:
    """Keep a person's conversations, or one conversation, out of every
    purge until the hold is lifted."""
    if (user_id is None) == (conversation_id is None):
        raise BadRequest("Hold either a person or a conversation", code="hold_target")
    if user_id is not None:
        member = await session.scalar(
            select(Membership).where(Membership.org_id == org_id, Membership.user_id == user_id)
        )
        if member is None:
            raise NotFound("Member not found")
    else:
        conv = await session.get(Conversation, conversation_id)
        if conv is None or conv.org_id != org_id:
            raise NotFound("Conversation not found")
    row = LegalHold(
        org_id=org_id,
        user_id=user_id,
        conversation_id=conversation_id,
        reason=reason.strip(),
        created_by=actor_id,
    )
    session.add(row)
    await session.flush()
    await audit.record(
        session,
        action="legal_hold.create",
        org_id=org_id,
        actor_user_id=actor_id,
        target_type="user" if user_id else "conversation",
        target_id=user_id or conversation_id,
        meta={"hold_id": str(row.id)},
        ip=ip,
    )
    await session.commit()
    await session.refresh(row)
    return row


async def release(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    hold_id: uuid.UUID,
    actor_id: uuid.UUID,
    ip: str | None,
) -> LegalHold:
    row = await session.get(LegalHold, hold_id)
    if row is None or row.org_id != org_id:
        raise NotFound("Hold not found")
    if row.released_at is None:
        row.released_at, row.released_by = datetime.now(UTC), actor_id
        await audit.record(
            session,
            action="legal_hold.release",
            org_id=org_id,
            actor_user_id=actor_id,
            target_type="user" if row.user_id else "conversation",
            target_id=row.user_id or row.conversation_id,
            meta={"hold_id": str(row.id)},
            ip=ip,
        )
        await session.commit()
        await session.refresh(row)
    return row


async def holds(session: AsyncSession, org_id: uuid.UUID) -> list[LegalHold]:
    """Holds in force first, then lifted ones, newest first."""
    return list(
        (
            await session.scalars(
                select(LegalHold)
                .where(LegalHold.org_id == org_id)
                .order_by(LegalHold.released_at.is_not(None), LegalHold.created_at.desc())
            )
        ).all()
    )


async def erasures(session: AsyncSession, org_id: uuid.UUID, limit: int = 100) -> list[Erasure]:
    return list(
        (
            await session.scalars(
                select(Erasure)
                .where(Erasure.org_id == org_id)
                .order_by(Erasure.created_at.desc())
                .limit(limit)
            )
        ).all()
    )


async def load_erasures(session: AsyncSession, rows: list[dict]) -> int:
    """Put back the record of erasures saved before a restore (JSON rows as
    `restore.sh` writes them), for those the restored database lacks.
    Returns how many were added. An organisation the backup doesn't have
    is skipped: there is nothing of it to erase."""
    have = set((await session.scalars(select(Erasure.id))).all())
    orgs = set((await session.scalars(select(Organization.id))).all())
    users = set((await session.scalars(select(User.id))).all())
    added = 0
    for row in rows:
        eid, org_id = uuid.UUID(str(row["id"])), uuid.UUID(str(row["org_id"]))
        if eid in have or org_id not in orgs:
            continue
        have.add(eid)
        by = uuid.UUID(str(row["created_by"])) if row.get("created_by") else None
        cutoff = row.get("cutoff")
        session.add(
            Erasure(
                id=eid,
                org_id=org_id,
                kind=str(row["kind"]),
                user_id=uuid.UUID(str(row["user_id"])) if row.get("user_id") else None,
                cutoff=datetime.fromisoformat(cutoff) if cutoff else None,
                conversation_ids=list(row.get("conversation_ids") or []),
                memories=int(row.get("memories") or 0),
                comments=int(row.get("comments") or 0),
                details=dict(row.get("details") or {}),
                created_by=by if by in users else None,
                created_at=datetime.fromisoformat(str(row["created_at"])),
            )
        )
        added += 1
    await session.commit()
    return added


async def reapply(session: AsyncSession) -> int:
    """After a restore: erase again what was erased since the backup was
    taken. Holds still apply. Returns how many conversations went."""
    total = 0
    rows = (await session.scalars(select(Erasure).order_by(Erasure.created_at))).all()
    for e in rows:
        ids = [uuid.UUID(str(c)) for c in e.conversation_ids or []]
        for start in range(0, len(ids), BATCH):
            total += len(await purge(session, e.org_id, ids[start : start + BATCH]))
        if e.kind == "person" and e.user_id is not None:
            await session.execute(_their_memories(e.org_id, e.user_id))
            await session.execute(
                delete(MessageFeedback)
                .where(MessageFeedback.org_id == e.org_id, MessageFeedback.user_id == e.user_id)
                .execution_options(synchronize_session=False)
            )
            await session.execute(
                update(UsageEvent)
                .where(UsageEvent.org_id == e.org_id, UsageEvent.user_id == e.user_id)
                .values(user_id=None)
                .execution_options(synchronize_session=False)
            )
            await session.commit()
    return total


__all__ = [
    "BATCH",
    "DAYS_MAX",
    "DAYS_MIN",
    "DPDP_FLOOR",
    "Erased",
    "apply_retention",
    "erase_person",
    "erasures",
    "hold",
    "holds",
    "purge",
    "reapply",
    "release",
    "retention_sweep",
]
