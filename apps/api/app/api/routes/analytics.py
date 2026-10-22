"""Analytics (Phase 10.1) and traffic on the canvas (Phase 10.2).

- An assistant's analytics are for the people who build it: its editors and
  admins (`EditableAssistantCtx`, as for its feedback in 8.8). Counts and
  spend only; never who asked or what.
- The managers' report covers every assistant of the organization: admins,
  as JSON for the Usage page and as CSV to take away (the export is
  recorded, like the audit log's).
- Traffic is drawn on the canvas, so it goes to whoever may open the
  canvas (`AssistantCtx`): counts per station, nothing else.
"""

from __future__ import annotations

import csv
import io
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response

from app.api.deps import AssistantCtx, ClientIP, EditableAssistantCtx, SessionDep, require_role
from app.models.enums import MemberRole
from app.models.membership import Membership
from app.schemas.analytics import (
    AnalyticsDay,
    AnalyticsFigures,
    AssistantAnalyticsOut,
    AssistantTrafficOut,
    NodeTrafficOut,
    OrgAnalyticsOut,
    OrgAnalyticsRow,
    TrafficTotalOut,
)
from app.services import analytics, audit, traffic

router = APIRouter(tags=["analytics"])

RequireAdmin = Depends(require_role(MemberRole.admin))

Days = Annotated[
    int,
    Query(ge=1, le=analytics.MAX_DAYS, description="Whole days, ending with today (UTC)."),
]


def _figures(f: analytics.Figures) -> AnalyticsFigures:
    return AnalyticsFigures(
        conversations=f.conversations,
        people=f.people,
        resolved=f.resolved,
        not_resolved=f.not_resolved,
        no_verdict=f.no_verdict,
        with_errors=f.with_errors,
        spend_usd=f.spend_usd,
        resolution_rate=f.resolution_rate,
        cost_per_resolved_usd=f.cost_per_resolved_usd,
    )


@router.get("/assistants/{assistant_id}/analytics", response_model=AssistantAnalyticsOut)
async def assistant_analytics(
    ctx: EditableAssistantCtx, session: SessionDep, days: Days = 30
) -> AssistantAnalyticsOut:
    """How its live conversations went (Phase 10.1): resolved, not resolved
    or no verdict, spend and cost per resolved conversation, a daily series,
    and the same figures for the period before, to compare."""
    out = await analytics.for_assistant(session, ctx.assistant, days=days)
    return AssistantAnalyticsOut(
        assistant_id=ctx.assistant.id,
        days=out.window.days,
        start=out.window.start,
        end=out.window.end,
        current=_figures(out.current),
        previous=_figures(out.previous),
        series=[AnalyticsDay(**vars(d)) for d in out.series],
    )


@router.get("/orgs/{org_id}/analytics", response_model=OrgAnalyticsOut)
async def org_analytics(
    org_id: Annotated[uuid.UUID, Path()],
    _: Annotated[Membership, RequireAdmin],
    session: SessionDep,
    days: Days = 30,
) -> OrgAnalyticsOut:
    """The managers' report (Phase 10.1): every assistant with its
    conversations, people, resolution rate, spend and cost per resolved
    conversation, against the period before, and the totals."""
    report = await analytics.for_org(session, org_id, days=days)
    return OrgAnalyticsOut(
        days=report.window.days,
        start=report.window.start,
        end=report.window.end,
        assistants=[
            OrgAnalyticsRow(
                assistant_id=r.assistant_id,
                name=r.name,
                status=r.status,
                current=_figures(r.current),
                previous=_figures(r.previous),
            )
            for r in report.rows
        ],
        total=_figures(report.total),
        previous_total=_figures(report.previous_total),
    )


def _cell(value: float | None, places: int) -> str:
    return "" if value is None else f"{value:.{places}f}"


@router.get("/orgs/{org_id}/analytics.csv", response_class=Response)
async def org_analytics_csv(
    org_id: Annotated[uuid.UUID, Path()],
    actor: Annotated[Membership, RequireAdmin],
    session: SessionDep,
    ip: ClientIP,
    days: Days = 30,
) -> Response:
    """The managers' report as CSV: one row per assistant, then the total.
    The export is recorded."""
    report = await analytics.for_org(session, org_id, days=days)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(
        [
            "assistant",
            "status",
            "conversations",
            "people",
            "resolved",
            "not_resolved",
            "no_verdict",
            "resolution_rate",
            "with_errors",
            "spend_usd",
            "cost_per_resolved_usd",
            "previous_conversations",
            "previous_resolution_rate",
            "previous_spend_usd",
        ]
    )

    def line(name: str, status: str, f: analytics.Figures, p: analytics.Figures) -> list[str]:
        return [
            name,
            status,
            str(f.conversations),
            str(f.people),
            str(f.resolved),
            str(f.not_resolved),
            str(f.no_verdict),
            _cell(f.resolution_rate, 4),
            str(f.with_errors),
            _cell(f.spend_usd, 6),
            _cell(f.cost_per_resolved_usd, 6),
            str(p.conversations),
            _cell(p.resolution_rate, 4),
            _cell(p.spend_usd, 6),
        ]

    for r in report.rows:
        writer.writerow(line(r.name, r.status, r.current, r.previous))
    writer.writerow(line("Total", "", report.total, report.previous_total))
    await audit.record(
        session,
        action="analytics.export",
        org_id=org_id,
        actor_user_id=actor.user_id,
        meta={"days": report.window.days, "assistants": len(report.rows)},
        ip=ip,
    )
    await session.commit()
    start = report.window.start.date().isoformat()
    return Response(
        content=out.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="assistant-analytics-{start}.csv"'},
    )


@router.get("/assistants/{assistant_id}/traffic", response_model=AssistantTrafficOut)
async def assistant_traffic(
    ctx: AssistantCtx,
    session: SessionDep,
    hours: Annotated[int, Query(ge=1, le=traffic.MAX_HOURS)] = 24,
) -> AssistantTrafficOut:
    """Traffic on the canvas (Phase 10.2): per station of the draft, the
    live calls, errors and approval requests in the last `hours`; the agent
    carries the turns. Counts only."""
    t = await traffic.for_assistant(session, ctx.assistant, hours=hours)
    return AssistantTrafficOut(
        assistant_id=ctx.assistant.id,
        hours=hours,
        since=t.since,
        nodes={k: NodeTrafficOut(**vars(v)) for k, v in t.nodes.items()},
        total=TrafficTotalOut(**vars(t.total)),
    )
