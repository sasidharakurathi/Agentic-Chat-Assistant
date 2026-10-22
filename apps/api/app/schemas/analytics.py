"""Analytics with a baseline (Phase 10.1) and traffic on the canvas (Phase
10.2). Definitions live in `services/analytics.py` and `services/traffic.py`."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from app.schemas.common import ApiModel


class AnalyticsFigures(ApiModel):
    #: Live conversations started in the period with a question in them.
    conversations: int
    #: Different people who started one.
    people: int
    #: The person said "Did this solve it?" yes.
    resolved: int
    #: They said no, or gave an answer a thumbs down and never said yes.
    not_resolved: int
    #: Neither: no verdict, counted as neither.
    no_verdict: int
    #: Conversations in which a turn ended in an error.
    with_errors: int
    spend_usd: float
    #: resolved / (resolved + not_resolved); null when nobody said.
    resolution_rate: float | None
    #: spend / resolved; null when nothing was resolved.
    cost_per_resolved_usd: float | None


class AnalyticsDay(ApiModel):
    date: date
    conversations: int
    resolved: int
    spend_usd: float


class AssistantAnalyticsOut(ApiModel):
    assistant_id: uuid.UUID
    days: int
    #: The period: whole UTC days, `end` exclusive (tomorrow at 00:00).
    start: datetime
    end: datetime
    current: AnalyticsFigures
    #: The same figures for the period of equal length just before.
    previous: AnalyticsFigures
    #: One entry per day of the period, oldest first.
    series: list[AnalyticsDay]


class OrgAnalyticsRow(ApiModel):
    assistant_id: uuid.UUID
    name: str
    status: str
    current: AnalyticsFigures
    previous: AnalyticsFigures


class OrgAnalyticsOut(ApiModel):
    days: int
    start: datetime
    end: datetime
    #: Every assistant of the organization, busiest first.
    assistants: list[OrgAnalyticsRow]
    total: AnalyticsFigures
    previous_total: AnalyticsFigures


class NodeTrafficOut(ApiModel):
    calls: int
    errors: int
    approvals: int


class TrafficTotalOut(ApiModel):
    #: Turns (one question, one answer) and those that ended in an error.
    turns: int
    turn_errors: int
    calls: int
    errors: int
    approvals: int
    #: Calls and requests the draft has no node for any more.
    unplaced: int


class AssistantTrafficOut(ApiModel):
    assistant_id: uuid.UUID
    hours: int
    since: datetime
    #: Canvas node id (of the draft graph) -> its counts. Nodes with no
    #: traffic are left out.
    nodes: dict[str, NodeTrafficOut]
    total: TrafficTotalOut


__all__ = [
    "AnalyticsDay",
    "AnalyticsFigures",
    "AssistantAnalyticsOut",
    "AssistantTrafficOut",
    "NodeTrafficOut",
    "OrgAnalyticsOut",
    "OrgAnalyticsRow",
    "TrafficTotalOut",
]
