"""Requests that wait for a person (task 3.8; durable since Phase 9.1, D17).

Until Phase 9.1 a request lived inside its turn: the turn waited up to five
minutes, held a slot and a database connection, and an unanswered request
was quietly declined. An admin who answered after lunch found nothing to
decide. Now:

1. **Submit.** The router stores the request (`submit`): the exact input
   sealed under the KEK with its sha256, a redacted copy to show, the
   assistant and version that asked, and who decides (`tier`). The turn is
   told it is waiting, says so, and ends.
2. **Decide.** The asker (a declared action on their own rows, or a
   builder's test write in a preview) or one of the assistant's approvers
   (admins by default; named groups or people; their delegates while they
   are away) approves or declines, naming the hash of what they saw. Nobody
   decides their own approver request. One conditional UPDATE decides it.
3. **Run.** An approval hands it to the executor (`services/executor.py`),
   which claims it, re-checks everything, runs it once, records what
   happened and posts it into the conversation.
4. **Sweep.** Every minute: expire what nobody decided (the asker is
   told), remind the approvers once, and settle anything a dead process
   left behind.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.actions import canonical
from app.agent.approvals import redact
from app.api.errors import BadRequest, Forbidden, NotFound
from app.db.pagination import PageResult, keyset_page
from app.db.session import get_sessionmaker
from app.logging import get_logger
from app.models.approval import (
    FINAL,
    OPEN,
    SEALED_KIND,
    Approval,
    ApprovalDelegation,
    ApprovalRisk,
    ApprovalStatus,
    ApprovalTier,
)
from app.models.assistant import Assistant
from app.models.conversation import Conversation, Message, MessageRole
from app.models.enums import MemberRole
from app.models.group import GroupMember
from app.models.membership import Membership
from app.models.user import User
from app.observability import metrics
from app.security.access import member_of
from app.security.crypto import SealedSecret, open_sealed, seal
from app.security.redact import strip_secrets
from app.services import audit

if TYPE_CHECKING:
    from app.agent.actions import Asker
    from app.schemas.assistant_config import AssistantConfig

log = get_logger(__name__)

#: How long the asker has to confirm their own request (Phase 9.1). It is
#: re-checked when it runs, so a late yes is still safe.
CONFIRM_TTL = timedelta(hours=24)
#: A request running longer than this belonged to a process that died: it
#: is marked failed, never retried by itself (it may have half happened).
STUCK_AFTER = timedelta(minutes=10)
#: An approved request no executor picked up in this long is handed over
#: again (the claim makes a second hand-over harmless).
UNCLAIMED_AFTER = timedelta(minutes=2)
#: How much of what a request did is kept.
RESULT_CHARS = 4_000
#: Longest an approver may hand their requests over for.
MAX_DELEGATION = timedelta(days=90)


def reference(row: Approval) -> str:
    """The short id people quote: R-1A2B3C."""
    return f"R-{row.id.hex[:6].upper()}"


def digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(payload).encode()).hexdigest()


def _sealed(row: Approval, payload: dict[str, Any]) -> None:
    box = seal(canonical(payload), kind=SEALED_KIND)
    row.sealed_ciphertext, row.sealed_dek, row.sealed_nonce = (
        box.ciphertext,
        box.dek_wrapped,
        box.nonce,
    )
    row.input_sha256 = digest(payload)


def unseal(row: Approval) -> dict[str, Any]:
    """The exact input, checked against its hash. Raises ValueError."""
    import json

    if row.sealed_ciphertext is None or row.sealed_dek is None or row.sealed_nonce is None:
        raise ValueError("the request's input is gone")
    text = open_sealed(
        SealedSecret(
            ciphertext=row.sealed_ciphertext, dek_wrapped=row.sealed_dek, nonce=row.sealed_nonce
        ),
        kind=SEALED_KIND,
    )
    payload: dict[str, Any] = json.loads(text)
    if digest(payload) != row.input_sha256:
        raise ValueError("the request's input doesn't match what was approved")
    return payload


WIPED = {"sealed_ciphertext": None, "sealed_dek": None, "sealed_nonce": None}


# ── submitting ───────────────────────────────────────────────


async def expiry_for(session: AsyncSession, org_id: uuid.UUID, tier: ApprovalTier) -> datetime:
    from app.services import org_settings

    if tier is ApprovalTier.asker:
        return datetime.now(UTC) + CONFIRM_TTL
    hours = (await org_settings.switches(session, org_id)).action_expiry_hours
    return datetime.now(UTC) + timedelta(hours=hours)


async def submit(
    session: AsyncSession,
    *,
    conversation: Conversation,
    tool_name: str,
    tool_input: dict[str, Any],
    risk: str,
    rationale: str,
    tier: ApprovalTier,
    title: str,
    tool_call_id: str | None = None,
    action_name: str | None = None,
    statement: str | None = None,
    shown_input: dict[str, Any] | None = None,
    request: dict[str, Any] | None = None,
) -> Approval:
    """Store a request with its exact input sealed (Phase 9.1). `request`:
    an HTTP action's rendered call (Phase 9.3), sealed with it."""
    from app.models.conversation import ConversationMode

    payload: dict[str, Any] = {"tool": tool_name, "input": tool_input, "statement": statement}
    if request is not None:
        payload["request"] = request
    row = Approval(
        conversation_id=conversation.id,
        org_id=conversation.org_id,
        assistant_id=conversation.assistant_id,
        assistant_version_id=conversation.assistant_version_id,
        preview=conversation.mode is ConversationMode.preview,
        requested_by=conversation.created_by,
        tool_name=tool_name,
        action_name=action_name,
        title=title[:200],
        tool_call_id=tool_call_id,
        # What people see is redacted; what runs is sealed. For SQL the
        # statement used to be stored as it was, credentials included.
        input=redact(shown_input if shown_input is not None else tool_input),
        risk=ApprovalRisk(risk),
        tier=tier,
        rationale=strip_secrets(rationale)[:4000],
        status=ApprovalStatus.pending,
        expires_at=await expiry_for(session, conversation.org_id, tier),
    )
    _sealed(row, payload)
    session.add(row)
    await session.flush()
    await audit.record(
        session,
        action="approval.submitted",
        org_id=row.org_id,
        actor_user_id=row.requested_by,
        target_type="approval",
        target_id=row.id,
        meta=_facts(row),
    )
    await session.commit()
    if tier is ApprovalTier.approver:
        # Never a reason to fail the request itself.
        try:
            from app.services import notify

            await notify.approval_waiting(session, row)
        except Exception:
            log.exception("approval_notify_failed", approval_id=str(row.id))
    return row


def _facts(row: Approval) -> dict[str, Any]:
    """What the audit log keeps of a request: enough to stand on its own
    when the conversation and the row are gone."""
    return {
        "tool": row.tool_name,
        "action": row.action_name,
        "tier": row.tier.value,
        "risk": row.risk.value,
        "input_sha256": row.input_sha256,
        "conversation_id": str(row.conversation_id) if row.conversation_id else None,
        "assistant_id": str(row.assistant_id) if row.assistant_id else None,
        "assistant_version_id": str(row.assistant_version_id) if row.assistant_version_id else None,
        "preview": row.preview,
        "requested_by": str(row.requested_by) if row.requested_by else None,
    }


# ── who decides ──────────────────────────────────────────────


async def named_approvers(session: AsyncSession, assistant_id: uuid.UUID | None) -> set[uuid.UUID]:
    """The people an assistant names (directly or through a group), active
    in its org. Empty: nobody named, so the admins decide."""
    if assistant_id is None:
        return set()
    assistant = await session.get(Assistant, assistant_id)
    if assistant is None:
        return set()
    chosen = assistant.action_approvers or {}
    people = {uuid.UUID(str(u)) for u in chosen.get("user_ids") or []}
    groups = [uuid.UUID(str(g)) for g in chosen.get("group_ids") or []]
    if groups:
        people |= set(
            (
                await session.scalars(
                    select(GroupMember.user_id).where(
                        GroupMember.group_id.in_(groups),
                        GroupMember.org_id == assistant.org_id,
                    )
                )
            ).all()
        )
    if not people:
        return set()
    active = (
        await session.scalars(
            select(Membership.user_id)
            .join(User, User.id == Membership.user_id)
            .where(
                Membership.org_id == assistant.org_id,
                Membership.user_id.in_(people),
                Membership.deactivated_at.is_(None),
                User.is_active.is_(True),
            )
        )
    ).all()
    return set(active)


async def delegates_of(
    session: AsyncSession, org_id: uuid.UUID, people: set[uuid.UUID]
) -> set[uuid.UUID]:
    """Who stands in for these people today."""
    if not people:
        return set()
    now = datetime.now(UTC)
    return set(
        (
            await session.scalars(
                select(ApprovalDelegation.delegate_id)
                .join(Membership, Membership.user_id == ApprovalDelegation.delegate_id)
                .where(
                    ApprovalDelegation.org_id == org_id,
                    ApprovalDelegation.user_id.in_(people),
                    ApprovalDelegation.revoked_at.is_(None),
                    ApprovalDelegation.until > now,
                    Membership.org_id == org_id,
                    Membership.deactivated_at.is_(None),
                )
            )
        ).all()
    )


async def _admins(session: AsyncSession, org_id: uuid.UUID) -> set[uuid.UUID]:
    return set(
        (
            await session.scalars(
                select(Membership.user_id)
                .join(User, User.id == Membership.user_id)
                .where(
                    Membership.org_id == org_id,
                    Membership.role.in_((MemberRole.admin, MemberRole.owner)),
                    Membership.deactivated_at.is_(None),
                    User.is_active.is_(True),
                )
            )
        ).all()
    )


async def deciders(session: AsyncSession, row: Approval) -> set[uuid.UUID]:
    """Everyone who may decide an approver request, the asker left out:
    the named approvers and their delegates, or the admins when nobody is
    named. Admins may always decide; they are emailed only when nobody else
    is named."""
    named = await named_approvers(session, row.assistant_id)
    people = named | await delegates_of(session, row.org_id, named) if named else set()
    if not people:
        people = await _admins(session, row.org_id)
    return people - {row.requested_by} if row.requested_by else people


@dataclass(frozen=True)
class Decider:
    """Who is deciding, for the record."""

    role: MemberRole
    #: The asker deciding their own approver request: only allowed when
    #: nobody else can, with the org's switch on and a note (as 7b.3).
    self_decided: bool = False


async def assert_can_decide(
    session: AsyncSession, approval: Approval, *, user_id: uuid.UUID
) -> Decider:
    """Who may decide a request (Phase 9.1, plan D7).

    - **The asker's own** (`tier=asker`): only the person who asked. It is
      their own row, or their own test in a preview.
    - **An approver's** (`tier=approver`): an admin or the owner, one of
      the assistant's named approvers, or someone standing in for one of
      them. Never the person who asked, unless nobody else at all can (the
      owner alone, with the org's switch on, and a note).

    Someone outside the org gets 404, so the id confirms nothing.
    """
    member = await member_of(
        session, approval.org_id, user_id, missing="Approval not found", chat=True
    )
    if approval.tier is ApprovalTier.asker:
        if approval.requested_by != user_id:
            raise Forbidden("Only the person who asked can confirm this.", code="asker_decides")
        return Decider(member.role)
    if approval.requested_by == user_id:
        if await deciders(session, approval):
            raise Forbidden(
                "Someone else decides your request: it waits for an approver.",
                code="cannot_decide_own",
            )
        from app.services import org_settings

        if not (await org_settings.switches(session, approval.org_id)).sole_reviewer_self_approval:
            raise Forbidden(
                "Nobody else can decide this, and deciding your own requests is switched off.",
                code="cannot_decide_own",
            )
        return Decider(member.role, self_decided=True)
    if member.role.satisfies(MemberRole.admin):
        return Decider(member.role)
    named = await named_approvers(session, approval.assistant_id)
    if user_id in named or user_id in await delegates_of(session, approval.org_id, named):
        return Decider(member.role)
    raise Forbidden("Only this assistant's approvers can decide this.", code="not_an_approver")


# ── deciding ─────────────────────────────────────────────────


async def get(session: AsyncSession, approval_id: uuid.UUID) -> Approval:
    row = await session.get(Approval, approval_id)
    if row is None:
        raise NotFound("Approval not found")
    return row


async def resolve(
    session: AsyncSession,
    approval: Approval,
    *,
    decision: str,
    user_id: uuid.UUID | None,
    decider: Decider | None = None,
    input_sha256: str | None = None,
    note: str | None = None,
    params: dict[str, Any] | None = None,
    ip: str | None = None,
) -> Approval:
    """Record a decision, then hand an approved request to the executor.

    One conditional UPDATE decides it (Phase 7a.7): only a row still pending
    and not past its expiry changes, so two deciders can't both win. The
    decision must name the hash of the input it was shown (Phase 9.1): if
    the request changed in between, it is refused rather than approving
    something nobody read. The asker may change a declared action's values
    on their own card (`params`); the statement is built again and checked
    again, and that is what runs.
    """
    if decision not in ("approved", "denied"):
        raise BadRequest("decision must be 'approved' or 'denied'", code="invalid_decision")
    decider = decider or Decider(MemberRole.admin)
    note = (note or "").strip() or None
    if approval.input_sha256 is not None and input_sha256 != approval.input_sha256:
        raise BadRequest(
            "This request has changed since you opened it. Open it again.",
            code="input_changed",
        )
    if decider.self_decided and not note:
        raise BadRequest("Say why you are deciding your own request", code="note_required")
    if decision == "denied" and approval.tier is ApprovalTier.approver and not note:
        raise BadRequest("Say why, so the person who asked knows", code="note_required")
    edited: dict[str, Any] = {}
    if params is not None:
        if decision != "approved" or approval.tier is not ApprovalTier.asker:
            raise BadRequest("Only the asker can change the values", code="not_editable")
        edited = await _edited(session, approval, params)

    now = datetime.now(UTC)
    won = await session.scalar(
        update(Approval)
        .where(
            Approval.id == approval.id,
            Approval.status == ApprovalStatus.pending,
            or_(Approval.expires_at.is_(None), Approval.expires_at > now),
        )
        .values(
            status=ApprovalStatus(decision),
            decided_by=user_id,
            decided_at=now,
            note=note,
            **edited,
            **(WIPED if decision == "denied" else {}),
        )
        .returning(Approval.id)
        .execution_options(synchronize_session=False)
    )
    if won is None:
        await session.rollback()
        await session.refresh(approval)
        if approval.status is not ApprovalStatus.pending:
            raise BadRequest(
                f"this approval was already {approval.status.value}", code="approval_not_pending"
            )
        raise BadRequest("this approval has expired", code="approval_expired")
    await session.refresh(approval)
    # In the same transaction as the decision: an audit entry exists exactly
    # when a decision was recorded.
    await audit.record(
        session,
        action=f"approval.{decision}",
        org_id=approval.org_id,
        actor_user_id=user_id,
        target_type="approval",
        target_id=approval.id,
        meta={
            **_facts(approval),
            "decider_role": decider.role.value,
            "self_approved": user_id is not None and user_id == approval.requested_by,
            "edited": bool(edited),
            "note": note,
        },
        ip=ip,
    )
    await session.commit()
    if approval.created_at is not None and approval.decided_at is not None:
        waited = (approval.decided_at - approval.created_at).total_seconds()
        metrics.APPROVAL_WAIT.observe(max(0.0, waited), decision)

    if decision == "approved":
        from app.services import executor

        await executor.dispatch(approval.id)
    else:
        await finished(session, approval, ApprovalStatus.denied)
    return approval


async def _edited(
    session: AsyncSession, approval: Approval, params: dict[str, Any]
) -> dict[str, Any]:
    """The asker's own values for a declared action, built and checked again."""
    from app.agent import actions
    from app.agent.actions import ActionInputError
    from app.services import executor

    if approval.action_name is None or approval.assistant_id is None:
        raise BadRequest("Only an action's values can be changed", code="not_editable")
    config = await executor.config_of(session, approval)
    found = actions.find(config, approval.tool_name) if config else None
    asker = await executor.asker_of(session, approval)
    if asker is None or config is None:
        raise BadRequest("This action is no longer available", code="not_editable")
    payload: dict[str, Any] = {"tool": approval.tool_name, "input": dict(params)}
    try:
        if found is not None:
            ref, action = found
            engine, _ = await actions.engine_of(approval.assistant_id, ref.connection_id)
            statement, values = actions.render(action, params, asker=asker, engine=engine)
        else:
            statement, values, payload["request"] = await _edited_call(
                approval, config, params, asker
            )
    except ActionInputError as exc:
        raise BadRequest(str(exc), code="invalid_values") from exc
    payload["statement"] = statement
    holder = Approval()
    _sealed(holder, payload)
    return {
        "sealed_ciphertext": holder.sealed_ciphertext,
        "sealed_dek": holder.sealed_dek,
        "sealed_nonce": holder.sealed_nonce,
        "input_sha256": holder.input_sha256,
        "input": redact(values),
        "rationale": strip_secrets(statement)[:4000],
    }


async def _edited_call(
    approval: Approval, config: AssistantConfig, params: dict[str, Any], asker: Asker
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """An HTTP action's call, rendered again with the asker's new values."""
    from app.agent import http_actions

    action = http_actions.find_http(config, approval.tool_name)
    conn = (
        await http_actions.connection(approval.assistant_id, action.connection_id)
        if action is not None and approval.assistant_id is not None
        else None
    )
    if action is None or conn is None:
        raise BadRequest("This action is no longer available", code="not_editable")
    call, values = http_actions.render(action, params, asker=asker)
    return call.statement(conn.base_url), values, {**call.payload(), "base_url": conn.base_url}


async def withdraw(
    session: AsyncSession, approval: Approval, *, user_id: uuid.UUID, ip: str | None = None
) -> Approval:
    """The asker takes back a request still waiting."""
    if approval.requested_by != user_id:
        raise Forbidden("Only the person who asked can withdraw it", code="not_your_request")
    won = await session.scalar(
        update(Approval)
        .where(Approval.id == approval.id, Approval.status == ApprovalStatus.pending)
        .values(status=ApprovalStatus.cancelled, decided_at=datetime.now(UTC), **WIPED)
        .returning(Approval.id)
        .execution_options(synchronize_session=False)
    )
    if won is None:
        await session.rollback()
        await session.refresh(approval)
        raise BadRequest(
            f"this request was already {approval.status.value}", code="approval_not_pending"
        )
    await session.refresh(approval)
    await audit.record(
        session,
        action="approval.withdrawn",
        org_id=approval.org_id,
        actor_user_id=user_id,
        target_type="approval",
        target_id=approval.id,
        meta=_facts(approval),
        ip=ip,
    )
    await session.commit()
    await finished(session, approval, ApprovalStatus.cancelled, notify_asker=False)
    return approval


# ── telling people how it ended ──────────────────────────────


def outcome_text(row: Approval, status: ApprovalStatus) -> str:
    """How it ended: a first line in plain words (all an employee is shown,
    `chat_view.message`), then what it did or why not (for builders, and for
    the model's next turn)."""
    what = f"{reference(row)} ({row.title or 'request'})"
    if status is ApprovalStatus.succeeded:
        return f"{what} was done." + (f"\n{row.result}" if row.result else "")
    if status is ApprovalStatus.failed:
        return f"{what} didn't go through." + (f"\n{row.result}" if row.result else "")
    if status is ApprovalStatus.denied:
        return f"{what} was declined." + (f" {row.note}" if row.note else "")
    if status is ApprovalStatus.expired:
        return f"{what} wasn't decided in time, so nothing was done."
    return f"{what} was withdrawn, so nothing was done."


async def finished(
    session: AsyncSession,
    row: Approval,
    status: ApprovalStatus,
    *,
    notify_asker: bool = True,
) -> None:
    """Post how a request ended into its conversation and, for an
    approver's request, email the asker. Never raises."""
    try:
        if row.conversation_id is not None:
            session.add(
                Message(
                    conversation_id=row.conversation_id,
                    org_id=row.org_id,
                    role=MessageRole.system,
                    content=outcome_text(row, status),
                    blocks=[
                        {
                            "type": "request",
                            "approval_id": str(row.id),
                            "status": status.value,
                        }
                    ],
                )
            )
            await session.commit()
        if notify_asker and row.tier is ApprovalTier.approver:
            from app.services import notify

            await notify.approval_outcome(session, row, status)
    except Exception:
        log.exception("approval_outcome_post_failed", approval_id=str(row.id))


async def unreported(session: AsyncSession, conversation_id: uuid.UUID) -> list[Approval]:
    """Requests that ended since the conversation's last turn, to tell the
    model about at the start of the next one."""
    return list(
        (
            await session.scalars(
                select(Approval)
                .where(
                    Approval.conversation_id == conversation_id,
                    Approval.status.in_(FINAL),
                    Approval.reported_at.is_(None),
                )
                .order_by(Approval.created_at)
            )
        ).all()
    )


async def mark_reported(session: AsyncSession, ids: list[uuid.UUID]) -> None:
    if ids:
        await session.execute(
            update(Approval)
            .where(Approval.id.in_(ids))
            .values(reported_at=datetime.now(UTC))
            .execution_options(synchronize_session=False)
        )


# ── listing ──────────────────────────────────────────────────


async def list_pending(
    session: AsyncSession, conversation_id: uuid.UUID, *, limit: int = 50, cursor: str | None = None
) -> PageResult[Approval]:
    """Still waiting in a conversation, oldest first."""
    return await keyset_page(
        session,
        select(Approval).where(
            Approval.conversation_id == conversation_id,
            Approval.status == ApprovalStatus.pending,
            # Past its expiry it can no longer be decided (Phase 7a.7), so it
            # is not offered, even before the sweep closes it.
            or_(Approval.expires_at.is_(None), Approval.expires_at > datetime.now(UTC)),
        ),
        [Approval.created_at, Approval.id],
        limit=limit,
        cursor=cursor,
        descending=False,
    )


async def list_in_conversation(
    session: AsyncSession, conversation_id: uuid.UUID, *, limit: int = 50, cursor: str | None = None
) -> PageResult[Approval]:
    """Every request in a conversation, in any state, oldest first: the
    cards in the thread show where each one is (Phase 9.1)."""
    return await keyset_page(
        session,
        select(Approval).where(Approval.conversation_id == conversation_id),
        [Approval.created_at, Approval.id],
        limit=limit,
        cursor=cursor,
        descending=False,
    )


async def list_decidable(
    session: AsyncSession, membership: Membership, *, limit: int = 50
) -> list[Approval]:
    """The approver requests this person may decide, oldest first: an
    admin's are all of them; an approver's are those of the assistants that
    name them, or someone they stand in for (Phase 9.1). Their own only when
    nobody else can decide it (`assert_can_decide`)."""
    rows = (
        await session.scalars(
            select(Approval)
            .where(
                Approval.org_id == membership.org_id,
                Approval.status == ApprovalStatus.pending,
                Approval.tier == ApprovalTier.approver,
                Approval.expires_at > datetime.now(UTC),
            )
            .order_by(Approval.created_at, Approval.id)
            .limit(500)
        )
    ).all()
    out = []
    for row in rows:
        try:
            await assert_can_decide(session, row, user_id=membership.user_id)
        except (Forbidden, NotFound):
            continue
        out.append(row)
        if len(out) >= limit:
            break
    return out


async def list_mine(
    session: AsyncSession, membership: Membership, *, limit: int = 50, cursor: str | None = None
) -> PageResult[Approval]:
    """Every request this person made in the org, newest first: My requests
    (Phase 9.2)."""
    return await keyset_page(
        session,
        select(Approval).where(
            Approval.org_id == membership.org_id, Approval.requested_by == membership.user_id
        ),
        [Approval.created_at, Approval.id],
        limit=limit,
        cursor=cursor,
        descending=True,
    )


async def list_decided_by(
    session: AsyncSession, membership: Membership, *, limit: int = 50, cursor: str | None = None
) -> PageResult[Approval]:
    """The requests this person decided, newest first (Phase 9.2)."""
    return await keyset_page(
        session,
        select(Approval).where(
            Approval.org_id == membership.org_id, Approval.decided_by == membership.user_id
        ),
        [Approval.created_at, Approval.id],
        limit=limit,
        cursor=cursor,
        descending=True,
    )


async def list_in_org(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    status: ApprovalStatus | None = None,
    assistant_id: uuid.UUID | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> PageResult[Approval]:
    """Every request in the org, newest first, for its admins (Phase 9.2)."""
    query = select(Approval).where(Approval.org_id == org_id)
    if status is not None:
        query = query.where(Approval.status == status)
    if assistant_id is not None:
        query = query.where(Approval.assistant_id == assistant_id)
    return await keyset_page(
        session,
        query,
        [Approval.created_at, Approval.id],
        limit=limit,
        cursor=cursor,
        descending=True,
    )


@dataclass(frozen=True)
class Viewer:
    """How someone may see one request (Phase 9.2)."""

    #: They may decide it now.
    can_decide: bool
    #: They see what it would do: they may decide it, decided it, or are an
    #: admin. Otherwise they are its asker and see it as an employee would
    #: when they are one.
    full: bool


async def viewer(session: AsyncSession, approval: Approval, membership: Membership) -> Viewer:
    """Whether and how this member sees a request: its asker, whoever may
    decide it or decided it, and admins. Anyone else in the org is refused
    (403); outside it, not found."""
    can_decide = False
    if approval.status is ApprovalStatus.pending:
        try:
            await assert_can_decide(session, approval, user_id=membership.user_id)
            can_decide = True
        except Forbidden:
            pass
    full = (
        can_decide
        or membership.role.satisfies(MemberRole.admin)
        or approval.decided_by == membership.user_id
    )
    if not full and approval.requested_by != membership.user_id:
        raise Forbidden("This request isn't yours to see.", code="not_your_request")
    return Viewer(can_decide=can_decide, full=full)


async def summary(session: AsyncSession, membership: Membership) -> tuple[int, int]:
    """(waiting for this person to decide, their own still open): the
    counts the navigation and Chat home show (Phase 9.2)."""
    from sqlalchemy import func

    to_decide = len(await list_decidable(session, membership))
    mine_open = await session.scalar(
        select(func.count(Approval.id)).where(
            Approval.org_id == membership.org_id,
            Approval.requested_by == membership.user_id,
            Approval.status.in_(OPEN),
        )
    )
    return to_decide, int(mine_open or 0)


# ── delegation ───────────────────────────────────────────────


async def delegations(
    session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID
) -> list[ApprovalDelegation]:
    """This person's hand-overs still running."""
    return list(
        (
            await session.scalars(
                select(ApprovalDelegation)
                .where(
                    ApprovalDelegation.org_id == org_id,
                    ApprovalDelegation.user_id == user_id,
                    ApprovalDelegation.revoked_at.is_(None),
                    ApprovalDelegation.until > datetime.now(UTC),
                )
                .order_by(ApprovalDelegation.created_at)
            )
        ).all()
    )


async def delegate(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    email: str,
    until: datetime,
    ip: str | None = None,
) -> ApprovalDelegation:
    """Hand one's requests to someone until a date (out of office)."""
    found = await session.scalar(
        select(User.id)
        .join(Membership, Membership.user_id == User.id)
        .where(
            User.email == email.strip().lower(),
            User.is_active.is_(True),
            Membership.org_id == org_id,
            Membership.deactivated_at.is_(None),
        )
    )
    if found is None:
        raise NotFound("Nobody with that email is a member here", code="person_not_found")
    delegate_id = found
    now = datetime.now(UTC)
    if until.tzinfo is None:
        until = until.replace(tzinfo=UTC)
    if until <= now:
        raise BadRequest("Choose a date in the future", code="delegation_past")
    if until - now > MAX_DELEGATION:
        raise BadRequest("At most 90 days at a time", code="delegation_too_long")
    if delegate_id == user_id:
        raise BadRequest("Choose someone else", code="delegation_self")
    row = ApprovalDelegation(org_id=org_id, user_id=user_id, delegate_id=delegate_id, until=until)
    session.add(row)
    await session.flush()
    await audit.record(
        session,
        action="approval.delegate",
        org_id=org_id,
        actor_user_id=user_id,
        target_type="user",
        target_id=delegate_id,
        meta={"until": until.isoformat()},
        ip=ip,
    )
    await session.commit()
    await session.refresh(row)
    return row


async def end_delegation(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    delegation_id: uuid.UUID,
    ip: str | None = None,
) -> None:
    """End one's own hand-over early. Someone else's is not found."""
    row = await session.get(ApprovalDelegation, delegation_id)
    if row is None or row.user_id != user_id or row.revoked_at is not None:
        raise NotFound("Not found")
    row.revoked_at = datetime.now(UTC)
    await audit.record(
        session,
        action="approval.delegate_end",
        org_id=row.org_id,
        actor_user_id=user_id,
        target_type="user",
        target_id=row.delegate_id,
        ip=ip,
    )
    await session.commit()


# ── the sweep ────────────────────────────────────────────────


async def sweep(now: datetime | None = None) -> dict[str, int]:
    """Every minute (the worker's `recovery_sweep`): expire what nobody
    decided, remind the approvers once, fail what a dead process left
    running, and hand over what no executor picked up."""
    now = now or datetime.now(UTC)
    counts = {"expired": 0, "reminded": 0, "stuck": 0, "handed_over": 0, "batched": 0}
    async with get_sessionmaker()() as session:
        expired = (
            await session.scalars(
                update(Approval)
                .where(Approval.status == ApprovalStatus.pending, Approval.expires_at < now)
                .values(status=ApprovalStatus.expired, decided_at=now, **WIPED)
                .returning(Approval)
                .execution_options(synchronize_session=False)
            )
        ).all()
        for row in expired:
            await audit.record(
                session,
                action="approval.expired",
                org_id=row.org_id,
                target_type="approval",
                target_id=row.id,
                meta=_facts(row),
            )
        await session.commit()
        for row in expired:
            await finished(session, row, ApprovalStatus.expired)
        counts["expired"] = len(expired)

        stuck = (
            await session.scalars(
                update(Approval)
                .where(
                    Approval.status == ApprovalStatus.executing,
                    Approval.executed_at < now - STUCK_AFTER,
                )
                .values(
                    status=ApprovalStatus.failed,
                    result="It stopped partway (the server restarted). Check the system it "
                    "writes to before asking again: it may or may not have happened.",
                    **WIPED,
                )
                .returning(Approval)
                .execution_options(synchronize_session=False)
            )
        ).all()
        for row in stuck:
            await audit.record(
                session,
                action="approval.failed",
                org_id=row.org_id,
                target_type="approval",
                target_id=row.id,
                meta={**_facts(row), "reason": "stuck"},
            )
        await session.commit()
        for row in stuck:
            await finished(session, row, ApprovalStatus.failed)
        counts["stuck"] = len(stuck)

        unclaimed = (
            await session.scalars(
                select(Approval.id).where(
                    Approval.status == ApprovalStatus.approved,
                    Approval.decided_at < now - UNCLAIMED_AFTER,
                )
            )
        ).all()
        counts["reminded"] = await _remind(session, now)
        # Requests that waited inside someone's quiet window (Phase 9.2).
        from app.services import notify

        counts["batched"] = await notify.send_batches(session, now)
    if unclaimed:
        from app.services import executor

        for approval_id in unclaimed:
            await executor.dispatch(approval_id)
        counts["handed_over"] = len(unclaimed)
    if any(counts.values()):
        log.info("approvals_swept", **counts)
    return counts


async def _remind(session: AsyncSession, now: datetime) -> int:
    """One reminder to the approvers, `action_reminder_hours` after asking."""
    from app.services import notify, org_settings

    rows = (
        await session.scalars(
            select(Approval).where(
                Approval.status == ApprovalStatus.pending,
                Approval.tier == ApprovalTier.approver,
                Approval.reminded_at.is_(None),
                Approval.expires_at > now,
            )
        )
    ).all()
    sent = 0
    for row in rows:
        hours = (await org_settings.switches(session, row.org_id)).action_reminder_hours
        if hours <= 0 or row.created_at > now - timedelta(hours=hours):
            continue
        claimed = await session.scalar(
            update(Approval)
            .where(Approval.id == row.id, Approval.reminded_at.is_(None))
            .values(reminded_at=now)
            .returning(Approval.id)
            .execution_options(synchronize_session=False)
        )
        await session.commit()
        if claimed is None:
            continue
        try:
            await notify.approval_waiting(session, row, reminder=True)
        except Exception:
            log.exception("approval_reminder_failed", approval_id=str(row.id))
        sent += 1
    return sent


async def sweep_orphaned(now: datetime | None = None) -> int:
    """The Phase 7a.7 name for the expiry part of the sweep."""
    return (await sweep(now))["expired"]


__all__ = [
    "CONFIRM_TTL",
    "Decider",
    "assert_can_decide",
    "deciders",
    "delegate",
    "delegates_of",
    "delegations",
    "digest",
    "end_delegation",
    "finished",
    "get",
    "list_decidable",
    "list_decided_by",
    "list_in_conversation",
    "list_in_org",
    "list_mine",
    "list_pending",
    "mark_reported",
    "named_approvers",
    "outcome_text",
    "reference",
    "resolve",
    "submit",
    "summary",
    "sweep",
    "sweep_orphaned",
    "unreported",
    "unseal",
    "viewer",
    "withdraw",
]
