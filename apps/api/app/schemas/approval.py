from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, computed_field

from app.models.approval import ApprovalRisk, ApprovalStatus, ApprovalTier
from app.schemas.common import ORMModel


class ApprovalResolve(BaseModel):
    decision: Literal["approved", "denied"]
    #: The hash of the input the decider was shown (Phase 9.1): refused if
    #: the request changed since, so what is approved is what was read.
    input_sha256: str | None = Field(default=None, max_length=64)
    #: Required to decline someone else's request; optional otherwise.
    note: str | None = Field(default=None, max_length=1000)
    #: The asker's own changes to a declared action's values, on their card.
    params: dict[str, Any] | None = None


class ApprovalOut(ORMModel):
    id: uuid.UUID
    conversation_id: uuid.UUID | None
    assistant_id: uuid.UUID | None = None
    preview: bool = False
    tool_name: str
    action_name: str | None = None
    title: str | None = None
    tool_call_id: str | None = None
    #: Already redacted when the row was written — see `approvals.redact`.
    input: dict[str, Any] = Field(default_factory=dict)
    risk: ApprovalRisk
    tier: ApprovalTier = ApprovalTier.approver
    rationale: str | None
    status: ApprovalStatus
    input_sha256: str | None = None
    requested_by: uuid.UUID | None = None
    decided_by: uuid.UUID | None
    decided_at: datetime | None
    note: str | None = None
    expires_at: datetime | None
    executed_at: datetime | None = None
    result: str | None = None
    created_at: datetime
    #: A declared action's values as the asker may change them on their card
    #: (filled in for their own requests still waiting; Phase 9.1).
    fields: list[ActionFieldOut] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def reference(self) -> str:
        """The short id people quote: R-1A2B3C."""
        return f"R-{self.id.hex[:6].upper()}"


class DecidableApprovalOut(ApprovalOut):
    """A request in someone's queue (Phase 9.1): who asked, and in which
    assistant. The conversation itself is not here: opening it is a read,
    recorded with a reason (7b.2)."""

    asker_name: str | None = None
    asker_email: str | None = None
    assistant_name: str | None = None
    #: Who decided it, once someone has (Phase 9.2).
    decided_by_name: str | None = None
    #: What the person reading may do with it now.
    can_decide: bool = False
    can_withdraw: bool = False


class ApprovalSummaryOut(BaseModel):
    """The counts the navigation and Chat home show (Phase 9.2)."""

    #: Waiting for this person to decide.
    to_decide: int
    #: Their own requests still waiting or running.
    mine_open: int


class ApproversIn(BaseModel):
    """Who decides an assistant's requests: empty means the admins."""

    group_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)
    user_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)


class ApproverPerson(BaseModel):
    user_id: uuid.UUID
    name: str | None
    email: str


class ApproversOut(BaseModel):
    group_ids: list[uuid.UUID]
    user_ids: list[uuid.UUID]
    #: Everyone who decides today, groups opened up (delegates not included).
    people: list[ApproverPerson]
    #: True when nobody is named, so the admins decide.
    admins_decide: bool


class ActionFieldOut(BaseModel):
    """One value of a declared action, for the asker's card (Phase 9.1)."""

    name: str
    type: str
    description: str = ""
    required: bool = True
    choices: list[str] = Field(default_factory=list)
    max_length: int = 500


class ActionCheckIn(BaseModel):
    """A declared action as the builder has it, before it is saved."""

    connection_id: str = Field(max_length=64)
    action: dict[str, Any]
    #: A SQL action on a database, or an HTTP action (Phase 9.3), whose
    #: connection is named in the action itself.
    kind: Literal["sql", "http"] = "sql"


class ActionCheckOut(BaseModel):
    ok: bool
    #: Why it can't be saved, in the builder's words.
    error: str | None = None
    #: Whether it changes data, and whether the connection may.
    writes: bool = False
    connection_can_write: bool | None = None


class DelegationIn(BaseModel):
    """Who stands in, by email (nobody needs the member list for this), and
    until when."""

    email: str = Field(min_length=3, max_length=320)
    until: datetime


class DelegationOut(ORMModel):
    id: uuid.UUID
    delegate_id: uuid.UUID
    delegate_name: str | None = None
    delegate_email: str | None = None
    until: datetime
    created_at: datetime


__all__ = [
    "ActionCheckIn",
    "ActionCheckOut",
    "ActionFieldOut",
    "ApprovalOut",
    "ApprovalResolve",
    "ApprovalSummaryOut",
    "ApproverPerson",
    "ApproversIn",
    "ApproversOut",
    "DecidableApprovalOut",
    "DelegationIn",
    "DelegationOut",
]
