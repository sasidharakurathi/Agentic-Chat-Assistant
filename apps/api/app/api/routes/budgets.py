"""Budgets (task 5.7): the org's day and month limits, and each assistant's.

Anyone in the org can see them and what has been spent (the dashboard and
the Panels show it). Only admins and owners change them, and every change
is audit-logged. Enforcement is in `services/budgets.py`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AssistantCtx, ClientIP, OrgMembership, SessionDep, require_role
from app.api.errors import Forbidden, NotFound
from app.models.assistant import Assistant
from app.models.budget import BudgetPeriod
from app.models.enums import MemberRole
from app.models.membership import Membership
from app.schemas.budget import (
    BudgetLimits,
    BudgetsOut,
    BudgetStatusOut,
    BuilderSpendOut,
    PeopleBudgetsOut,
    PersonBudgetOut,
    PersonDefaultIn,
    PersonDefaultOut,
)
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


async def _with_builder(session: AsyncSession, org_id: uuid.UUID, out: BudgetsOut) -> BudgetsOut:
    """The organisation's page adds builder testing's line (Phase 9.7)."""
    now = datetime.now(UTC)
    start, resets = svc.period_bounds(BudgetPeriod.month, now)
    status = await svc.builder_status(session, org_id, now)
    spent = (
        status.spent_usd if status is not None else await svc.builder_spent(session, org_id, start)
    )
    out.builder = BuilderSpendOut(
        spent_usd=round(spent, 6),
        limit_usd=status.limit_usd if status else None,
        ratio=round(status.ratio, 4) if status else None,
        state=status.state if status else "ok",
        resets_at=resets,
    )
    return out


def _limits(body: BudgetLimits) -> dict[BudgetPeriod, float | None]:
    return {BudgetPeriod.day: body.daily_usd, BudgetPeriod.month: body.monthly_usd}


@router.get("/orgs/{org_id}/budgets", response_model=BudgetsOut)
async def get_org_budgets(
    org_id: Annotated[uuid.UUID, Path()], m: OrgMembership, session: SessionDep
) -> BudgetsOut:
    """Every budget in the org (its own and each assistant's), with spend,
    and builder testing's spend this month (Phase 9.7)."""
    out = await _out(session, await svc.list_for_org(session, org_id), _is_admin(m))
    return await _with_builder(session, org_id, out)


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
    out = await _out(session, await svc.list_for_org(session, org_id), True)
    return await _with_builder(session, org_id, out)


async def _people_out(session: AsyncSession, org_id: uuid.UUID) -> PeopleBudgetsOut:
    defaults, rows = await svc.people(session, org_id)
    day, month = BudgetPeriod.day, BudgetPeriod.month
    return PeopleBudgetsOut(
        defaults=[
            PersonDefaultOut(
                role="employee" if key == svc.ROLE_EMPLOYEE else "builder",
                daily_usd=defaults[key].get(day),
                monthly_usd=defaults[key].get(month),
            )
            for key in svc.ROLE_KEYS
        ],
        people=[
            PersonBudgetOut(
                user_id=r.user_id,
                name=r.name,
                email=r.email,
                role=r.role.value,
                spent_today_usd=round(r.spent_day, 6),
                spent_month_usd=round(r.spent_month, 6),
                daily_usd=r.limits[day][0] if day in r.limits else None,
                monthly_usd=r.limits[month][0] if month in r.limits else None,
                own_daily=r.limits.get(day, (0, False))[1],
                own_monthly=r.limits.get(month, (0, False))[1],
            )
            for r in rows
        ],
    )


@router.get("/orgs/{org_id}/budgets/people", response_model=PeopleBudgetsOut)
async def get_people_budgets(
    org_id: Annotated[uuid.UUID, Path()], m: OrgAdmin, session: SessionDep
) -> PeopleBudgetsOut:
    """Per-person caps and everyone's spend (Phase 8.9): the admins' alone,
    since one person's spend is about that person."""
    return await _people_out(session, org_id)


@router.put("/orgs/{org_id}/budgets/people", response_model=PeopleBudgetsOut)
async def put_people_default(
    org_id: Annotated[uuid.UUID, Path()],
    body: PersonDefaultIn,
    m: OrgAdmin,
    session: SessionDep,
    ip: ClientIP,
) -> PeopleBudgetsOut:
    """The cap each Employee gets, or each builder, admin and owner. Null
    removes that limit."""
    await svc.set_person_default(
        session,
        org_id=org_id,
        key=svc.ROLE_EMPLOYEE if body.role == "employee" else svc.ROLE_BUILDER,
        limits=_limits(body),
        actor_id=m.user_id,
        ip=ip,
    )
    return await _people_out(session, org_id)


@router.put("/orgs/{org_id}/members/{user_id}/budget", response_model=PeopleBudgetsOut)
async def put_person_budget(
    org_id: Annotated[uuid.UUID, Path()],
    user_id: Annotated[uuid.UUID, Path()],
    body: BudgetLimits,
    m: OrgAdmin,
    session: SessionDep,
    ip: ClientIP,
) -> PeopleBudgetsOut:
    """One person's own cap, in place of their role's. Both null: back to
    the role's."""
    member = await session.scalar(
        select(Membership).where(Membership.org_id == org_id, Membership.user_id == user_id)
    )
    if member is None:
        raise NotFound("Member not found")
    await svc.set_person_override(
        session,
        org_id=org_id,
        user_id=user_id,
        limits=_limits(body),
        actor_id=m.user_id,
        ip=ip,
    )
    return await _people_out(session, org_id)


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
