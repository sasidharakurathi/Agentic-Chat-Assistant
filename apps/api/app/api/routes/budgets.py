"""Budgets (task 5.7): the org's day and month limits, and each assistant's.

Anyone in the org can see them and what has been spent (the dashboard and
the Panels show it). Only admins and owners change them, and every change
is audit-logged. Enforcement is in `services/budgets.py`.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AssistantCtx, ClientIP, OrgMembership, SessionDep, require_role
from app.api.errors import Forbidden
from app.models.assistant import Assistant
from app.models.budget import BudgetPeriod
from app.models.enums import MemberRole
from app.models.membership import Membership
from app.schemas.budget import BudgetLimits, BudgetsOut, BudgetStatusOut
from app.services import budgets as svc

router = APIRouter(tags=["budgets"])

OrgAdmin = Annotated[Membership, Depends(require_role(MemberRole.admin))]


def _is_admin(m: Membership) -> bool:
    return m.role.satisfies(MemberRole.admin)


async def _out(
    session: AsyncSession, statuses: list[svc.BudgetStatus], can_edit: bool
) -> BudgetsOut:
    ids = {s.assistant_id for s in statuses if s.assistant_id is not None}
    names: dict[uuid.UUID, str] = {}
    if ids:
        rows = await session.execute(
            select(Assistant.id, Assistant.name).where(Assistant.id.in_(ids))
        )
        names = dict(rows.tuples().all())
    return BudgetsOut(
        budgets=[
            BudgetStatusOut(
                scope=s.scope,
                assistant_id=s.assistant_id,
                assistant_name=names.get(s.assistant_id) if s.assistant_id else None,
                period=s.period.value,
                limit_usd=s.limit_usd,
                spent_usd=round(s.spent_usd, 6),
                ratio=round(s.ratio, 4),
                state=s.state,
                resets_at=s.resets_at,
            )
            for s in statuses
        ],
        can_edit=can_edit,
    )


def _limits(body: BudgetLimits) -> dict[BudgetPeriod, float | None]:
    return {BudgetPeriod.day: body.daily_usd, BudgetPeriod.month: body.monthly_usd}


@router.get("/orgs/{org_id}/budgets", response_model=BudgetsOut)
async def get_org_budgets(
    org_id: Annotated[uuid.UUID, Path()], m: OrgMembership, session: SessionDep
) -> BudgetsOut:
    """Every budget in the org (its own and each assistant's), with spend."""
    return await _out(session, await svc.list_for_org(session, org_id), _is_admin(m))


@router.put("/orgs/{org_id}/budgets", response_model=BudgetsOut)
async def put_org_budgets(
    org_id: Annotated[uuid.UUID, Path()],
    body: BudgetLimits,
    m: OrgAdmin,
    session: SessionDep,
    ip: ClientIP,
) -> BudgetsOut:
    await svc.set_limits(
        session,
        org_id=org_id,
        assistant_id=None,
        limits=_limits(body),
        actor_id=m.user_id,
        ip=ip,
    )
    return await _out(session, await svc.list_for_org(session, org_id), True)


@router.get("/assistants/{assistant_id}/budget", response_model=BudgetsOut)
async def get_assistant_budget(ctx: AssistantCtx, session: SessionDep) -> BudgetsOut:
    """What applies to this assistant: the org's budgets and its own."""
    a = ctx.assistant
    g = await svc.gate(session, a.org_id, a.id)
    return await _out(session, g.statuses, _is_admin(ctx.membership))


@router.put("/assistants/{assistant_id}/budget", response_model=BudgetsOut)
async def put_assistant_budget(
    body: BudgetLimits, ctx: AssistantCtx, session: SessionDep, ip: ClientIP
) -> BudgetsOut:
    if not _is_admin(ctx.membership):
        raise Forbidden("Only admins and owners can change budgets", code="insufficient_role")
    a = ctx.assistant
    await svc.set_limits(
        session,
        org_id=a.org_id,
        assistant_id=a.id,
        limits=_limits(body),
        actor_id=ctx.membership.user_id,
        ip=ip,
    )
    g = await svc.gate(session, a.org_id, a.id)
    return await _out(session, g.statuses, True)


__all__ = ["router"]
