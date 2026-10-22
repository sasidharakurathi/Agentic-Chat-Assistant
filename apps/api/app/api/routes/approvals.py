"""Requests that wait for a person (task 3.8; Phase 9.1, plan D7/D17).

- The cards in a conversation: `GET /conversations/{id}/approvals`.
- Deciding one: `POST /approvals/{id}:resolve`, naming the hash of what was
  shown; withdrawing your own: `POST /approvals/{id}:withdraw`.
- The queue of what you may decide: `GET /approvals/decidable`; what you
  decided, `/approvals/decided`; what you asked for, `/approvals/mine`; an
  organisation's, `GET /orgs/{org}/approvals` (admins); one request,
  `GET /approvals/{id}`, where emails link; the counts, `/approvals/summary`
  (Phase 9.2).
- Who decides an assistant's requests: `GET/PUT /assistants/{id}/approvers`.
- Handing your requests to someone while away: `/approvals/delegations`.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select

from app.api.deps import (
    AdminAssistantCtx,
    ChatMembership,
    ChatReadableConversationCtx,
    ClientIP,
    CurrentUser,
    EditableAssistantCtx,
    PageQuery,
    SessionDep,
    require_role,
)
from app.api.errors import BadRequest
from app.models.approval import Approval, ApprovalStatus
from app.models.assistant import Assistant
from app.models.enums import MemberRole
from app.models.group import Group
from app.models.membership import Membership
from app.models.user import User
from app.schemas.approval import (
    ActionCheckIn,
    ActionCheckOut,
    ActionFieldOut,
    ApprovalOut,
    ApprovalResolve,
    ApprovalSummaryOut,
    ApproverPerson,
    ApproversIn,
    ApproversOut,
    DecidableApprovalOut,
    DelegationIn,
    DelegationOut,
)
from app.schemas.common import Page
from app.services import approvals as svc
from app.services import audit, chat_view

router = APIRouter(tags=["approvals"])

RequireAdmin = Depends(require_role(MemberRole.admin))


@router.get("/conversations/{conversation_id}/approvals", response_model=Page[ApprovalOut])
async def list_for_conversation(
    ctx: ChatReadableConversationCtx,
    session: SessionDep,
    page: PageQuery,
    all_states: Annotated[bool, Query(alias="all")] = False,
) -> Page[ApprovalOut]:
    """A conversation's requests: those still waiting, or with `all=true`
    every one in any state (the cards in the thread say where each stands).

    The `approval_required` event only reaches whoever was watching the
    stream; reloading the page, or another tab, must still show them."""
    lister = svc.list_in_conversation if all_states else svc.list_pending
    rows = await lister(session, ctx.conversation.id, limit=page.limit, cursor=page.cursor)
    items = [ApprovalOut.model_validate(r) for r in rows.items]
    for item, row in zip(items, rows.items, strict=True):
        # A declared action's values, named as the action names them; the
        # asker's own card makes them editable while it waits.
        if row.action_name:
            item.fields = await _fields(session, row)
    if chat_view.shaped(ctx.membership):
        items = [chat_view.approval(a) for a in items]
    return Page(items=items, next_cursor=rows.next_cursor)


async def _fields(session: SessionDep, row: Approval) -> list[ActionFieldOut]:
    """The action's values as its asker may change them."""
    from app.agent import actions
    from app.services import executor

    config = await executor.config_of(session, row)
    found = actions.any_action(config, row.tool_name) if config else None
    if found is None:
        return []
    return [
        ActionFieldOut(
            name=p.name,
            type=p.type,
            description=p.description,
            required=p.required,
            choices=list(p.choices),
            max_length=p.max_length,
        )
        for p in found.params
    ]


@router.post("/assistants/{assistant_id}/actions:check", response_model=ActionCheckOut)
async def check_action(
    body: ActionCheckIn, ctx: EditableAssistantCtx, session: SessionDep
) -> ActionCheckOut:
    """Check a declared action before it is saved (Phase 9.1): the builder
    gets the reason at once, rather than a refused save of the whole canvas."""
    from pydantic import ValidationError

    from app.agent.actions import _parse_any
    from app.datasources.sql_guard import Permissions
    from app.models.integration import DbConnection
    from app.schemas.assistant_config import DeclaredAction

    if body.kind == "http":
        return await _check_http(body, ctx, session)
    try:
        action = DeclaredAction.model_validate(body.action)
    except ValidationError as exc:
        return ActionCheckOut(ok=False, error=_first_error(exc))
    import sqlglot.expressions as exp

    writes = isinstance(_parse_any(action.sql), (exp.Insert, exp.Update, exp.Delete))
    can_write = None
    try:
        conn = await session.scalar(
            select(DbConnection).where(
                DbConnection.id == uuid.UUID(body.connection_id),
                DbConnection.assistant_id == ctx.assistant.id,
            )
        )
    except ValueError:
        conn = None
    if conn is not None:
        can_write = Permissions.from_dict(conn.permissions).write
    return ActionCheckOut(ok=True, writes=writes, connection_can_write=can_write)


def _first_error(exc: Any) -> str:
    first = exc.errors()[0]
    where = ".".join(str(p) for p in first.get("loc", ()) if p != "__root__")
    message = str(first.get("msg", "invalid")).removeprefix("Value error, ")
    return f"{where}: {message}" if where else message


async def _check_http(
    body: ActionCheckIn, ctx: EditableAssistantCtx, session: SessionDep
) -> ActionCheckOut:
    """An HTTP action (Phase 9.3): its shape, its connection, and, for one
    the asker confirms, that the system is told who they are."""
    from pydantic import ValidationError

    from app.api.errors import NotFound
    from app.schemas.assistant_config import HttpAction
    from app.services import http_connections

    try:
        action = HttpAction.model_validate(body.action)
    except ValidationError as exc:
        return ActionCheckOut(ok=False, error=_first_error(exc))
    try:
        conn = await http_connections.get(
            session, assistant_id=ctx.assistant.id, connection_id=uuid.UUID(action.connection_id)
        )
    except (ValueError, NotFound):
        return ActionCheckOut(ok=False, error="Pick one of this assistant's HTTP connections.")
    if action.tier == "confirm" and not conn.send_asker:
        return ActionCheckOut(
            ok=False,
            error=f"'{conn.name}' doesn't tell the system who is asking, so the person can't "
            "confirm this as themselves. Turn on 'Say who is asking' on the connection, or let "
            "an approver decide.",
        )
    return ActionCheckOut(ok=True, writes=action.method != "GET")


@router.post("/approvals/{approval_id}:resolve", response_model=ApprovalOut)
async def resolve(
    body: ApprovalResolve,
    approval_id: uuid.UUID,
    user: CurrentUser,
    session: SessionDep,
    ip: ClientIP,
) -> ApprovalOut:
    row = await svc.get(session, approval_id)
    # The asker for their own; an approver (never the asker) for the rest.
    decider = await svc.assert_can_decide(session, row, user_id=user.id)
    row = await svc.resolve(
        session,
        row,
        decision=body.decision,
        user_id=user.id,
        decider=decider,
        input_sha256=body.input_sha256,
        note=body.note,
        params=body.params,
        ip=ip,
    )
    return ApprovalOut.model_validate(row)


@router.post("/approvals/{approval_id}:withdraw", response_model=ApprovalOut)
async def withdraw(
    approval_id: uuid.UUID, user: CurrentUser, session: SessionDep, ip: ClientIP
) -> ApprovalOut:
    """The asker takes back a request still waiting."""
    row = await svc.get(session, approval_id)
    from app.security.access import member_of

    await member_of(session, row.org_id, user.id, missing="Approval not found", chat=True)
    row = await svc.withdraw(session, row, user_id=user.id, ip=ip)
    return ApprovalOut.model_validate(row)


async def _described(
    session: SessionDep,
    rows: list[Approval],
    membership: Membership,
    *,
    full: bool | None = None,
) -> list[DecidableApprovalOut]:
    """Requests as the person reading sees them: who asked, in which
    assistant, who decided, what they may do now. `full=None` works out,
    per request, whether they see what it would do (`svc.viewer`); an
    employee who only asked sees it as Chat shows it (Phase 8.1)."""
    ids = {r.requested_by for r in rows if r.requested_by} | {
        r.decided_by for r in rows if r.decided_by
    }
    people = (
        {u.id: u for u in (await session.scalars(select(User).where(User.id.in_(ids)))).all()}
        if ids
        else {}
    )
    assistant_ids = {r.assistant_id for r in rows if r.assistant_id}
    names = (
        {
            a.id: a.name
            for a in (
                await session.scalars(select(Assistant).where(Assistant.id.in_(assistant_ids)))
            ).all()
        }
        if assistant_ids
        else {}
    )
    out = []
    for r in rows:
        seen = await svc.viewer(session, r, membership) if full is None else None
        shown = ApprovalOut.model_validate(r)
        if r.action_name:
            shown.fields = await _fields(session, r)
        sees_all = full if full is not None else bool(seen and seen.full)
        if not sees_all and chat_view.shaped(membership):
            shown = chat_view.approval(shown)
        asker = people.get(r.requested_by) if r.requested_by else None
        decider = people.get(r.decided_by) if r.decided_by else None
        pending = r.status is ApprovalStatus.pending
        out.append(
            DecidableApprovalOut.model_validate(
                {
                    **shown.model_dump(exclude={"reference"}),
                    "asker_name": asker.name if asker else None,
                    "asker_email": asker.email if asker else None,
                    "assistant_name": names.get(r.assistant_id) if r.assistant_id else None,
                    "decided_by_name": (decider.name or decider.email) if decider else None,
                    "can_decide": seen.can_decide if seen else (full is True and pending),
                    "can_withdraw": pending and r.requested_by == membership.user_id,
                }
            )
        )
    return out


@router.get("/approvals/decidable", response_model=list[DecidableApprovalOut])
async def decidable(membership: ChatMembership, session: SessionDep) -> list[DecidableApprovalOut]:
    """The requests waiting for this person, oldest first: an admin's are
    all approver requests; an approver's are those of the assistants that
    name them (or someone they stand in for). Each with what would run, so
    it can be decided here; the conversation itself is not included."""
    rows = await svc.list_decidable(session, membership)
    return await _described(session, rows, membership, full=True)


@router.get("/approvals/summary", response_model=ApprovalSummaryOut)
async def summary(membership: ChatMembership, session: SessionDep) -> ApprovalSummaryOut:
    """How many wait for this person to decide, and how many of their own
    are still open (Phase 9.2): the counts the navigation shows."""
    to_decide, mine_open = await svc.summary(session, membership)
    return ApprovalSummaryOut(to_decide=to_decide, mine_open=mine_open)


@router.get("/approvals/mine", response_model=Page[DecidableApprovalOut])
async def mine(
    membership: ChatMembership, session: SessionDep, page: PageQuery
) -> Page[DecidableApprovalOut]:
    """My requests (Phase 9.2): everything this person asked for, newest
    first, in any state. An employee sees each as Chat shows it."""
    rows = await svc.list_mine(session, membership, limit=page.limit, cursor=page.cursor)
    items = await _described(session, list(rows.items), membership, full=False)
    return Page(items=items, next_cursor=rows.next_cursor)


@router.get("/approvals/decided", response_model=Page[DecidableApprovalOut])
async def decided(
    membership: ChatMembership, session: SessionDep, page: PageQuery
) -> Page[DecidableApprovalOut]:
    """The requests this person decided, newest first (Phase 9.2)."""
    rows = await svc.list_decided_by(session, membership, limit=page.limit, cursor=page.cursor)
    items = await _described(session, list(rows.items), membership, full=True)
    return Page(items=items, next_cursor=rows.next_cursor)


@router.get("/orgs/{org_id}/approvals", response_model=Page[DecidableApprovalOut])
async def in_org(
    org_id: uuid.UUID,
    actor: Annotated[Membership, RequireAdmin],
    session: SessionDep,
    page: PageQuery,
    status_: Annotated[ApprovalStatus | None, Query(alias="status")] = None,
    assistant_id: uuid.UUID | None = None,
) -> Page[DecidableApprovalOut]:
    """Every request in the organisation, newest first, for its admins
    (Phase 9.2), by state and by assistant. The request, never the
    conversation."""
    rows = await svc.list_in_org(
        session,
        org_id,
        status=status_,
        assistant_id=assistant_id,
        limit=page.limit,
        cursor=page.cursor,
    )
    items = await _described(session, list(rows.items), actor, full=None)
    return Page(items=items, next_cursor=rows.next_cursor)


# ── who decides an assistant's requests ──────────────────────


async def _approvers_out(session: SessionDep, assistant: Assistant) -> ApproversOut:
    chosen = assistant.action_approvers or {}
    people_ids = await svc.named_approvers(session, assistant.id)
    users = (
        (
            await session.scalars(select(User).where(User.id.in_(people_ids)).order_by(User.email))
        ).all()
        if people_ids
        else []
    )
    return ApproversOut(
        group_ids=[uuid.UUID(str(g)) for g in chosen.get("group_ids") or []],
        user_ids=[uuid.UUID(str(u)) for u in chosen.get("user_ids") or []],
        people=[ApproverPerson(user_id=u.id, name=u.name, email=u.email) for u in users],
        admins_decide=not people_ids,
    )


@router.get("/assistants/{assistant_id}/approvers", response_model=ApproversOut)
async def get_approvers(ctx: EditableAssistantCtx, session: SessionDep) -> ApproversOut:
    """Who decides this assistant's requests. Its editors may see it; only
    an admin changes it."""
    return await _approvers_out(session, ctx.assistant)


@router.put("/assistants/{assistant_id}/approvers", response_model=ApproversOut)
async def set_approvers(
    body: ApproversIn,
    ctx: AdminAssistantCtx,
    user: CurrentUser,
    session: SessionDep,
    ip: ClientIP,
) -> ApproversOut:
    """Name the groups and people who decide this assistant's requests
    (Phase 9.1). Being one doesn't make someone an admin: they decide these
    requests and see each request, not every conversation. Empty: the
    admins decide."""
    org_id = ctx.assistant.org_id
    groups = set(body.group_ids)
    if groups:
        found = set(
            (
                await session.scalars(
                    select(Group.id).where(Group.id.in_(groups), Group.org_id == org_id)
                )
            ).all()
        )
        if found != groups:
            raise BadRequest("One of those groups isn't in this organisation", code="unknown_group")
    people = set(body.user_ids)
    if people:
        found = set(
            (
                await session.scalars(
                    select(Membership.user_id).where(
                        Membership.org_id == org_id,
                        Membership.user_id.in_(people),
                        Membership.deactivated_at.is_(None),
                    )
                )
            ).all()
        )
        if found != people:
            raise BadRequest("One of those people isn't a member here", code="unknown_person")
    before = dict(ctx.assistant.action_approvers or {})
    after = {
        "group_ids": sorted(str(g) for g in groups),
        "user_ids": sorted(str(u) for u in people),
    }
    ctx.assistant.action_approvers = after
    await audit.record(
        session,
        action="assistant.approvers.update",
        org_id=org_id,
        actor_user_id=user.id,
        target_type="assistant",
        target_id=ctx.assistant.id,
        meta={"from": before, "to": after},
        ip=ip,
    )
    await session.commit()
    await session.refresh(ctx.assistant)
    return await _approvers_out(session, ctx.assistant)


# ── standing in while someone is away ────────────────────────


async def _delegations_out(session: SessionDep, membership: Membership) -> list[DelegationOut]:
    rows = await svc.delegations(session, membership.org_id, membership.user_id)
    users = {
        u.id: u
        for u in (
            await session.scalars(select(User).where(User.id.in_({r.delegate_id for r in rows})))
        ).all()
    }
    return [
        DelegationOut(
            id=r.id,
            delegate_id=r.delegate_id,
            delegate_name=users[r.delegate_id].name if r.delegate_id in users else None,
            delegate_email=users[r.delegate_id].email if r.delegate_id in users else None,
            until=r.until,
            created_at=r.created_at,
        )
        for r in rows
    ]


@router.get("/approvals/delegations", response_model=list[DelegationOut])
async def my_delegations(membership: ChatMembership, session: SessionDep) -> list[DelegationOut]:
    """Who stands in for me, and until when."""
    return await _delegations_out(session, membership)


@router.post(
    "/approvals/delegations",
    response_model=list[DelegationOut],
    status_code=status.HTTP_201_CREATED,
)
async def add_delegation(
    body: DelegationIn, membership: ChatMembership, session: SessionDep, ip: ClientIP
) -> list[DelegationOut]:
    """Hand my requests to someone until a date (out of office). They decide
    what I could as a named approver; an admin's own powers don't pass on."""
    await svc.delegate(
        session,
        org_id=membership.org_id,
        user_id=membership.user_id,
        email=body.email,
        until=body.until,
        ip=ip,
    )
    return await _delegations_out(session, membership)


@router.delete("/approvals/delegations/{delegation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def end_delegation(
    delegation_id: uuid.UUID, user: CurrentUser, session: SessionDep, ip: ClientIP
) -> None:
    """End my hand-over early. By its id alone: someone else's isn't found."""
    await svc.end_delegation(session, user_id=user.id, delegation_id=delegation_id, ip=ip)


@router.get("/approvals/{approval_id}", response_model=DecidableApprovalOut)
async def one(
    approval_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> DecidableApprovalOut:
    """One request (Phase 9.2): where an email links. For its asker, whoever
    may decide it or decided it, and admins; refused to anyone else in the
    organisation, and not found outside it. Opening it is not a read of the
    conversation, which it doesn't include."""
    from app.db.tenancy import bind_org
    from app.security.access import member_of

    row = await svc.get(session, approval_id)
    membership = await member_of(
        session, row.org_id, user.id, missing="Approval not found", chat=True
    )
    bind_org(session, row.org_id)
    (out,) = await _described(session, [row], membership)
    return out


__all__ = ["router"]
