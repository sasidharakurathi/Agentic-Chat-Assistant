from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models.approval import ApprovalRisk, ApprovalStatus
from app.schemas.common import ORMModel


class ApprovalResolve(BaseModel):
    decision: Literal["approved", "denied"]


class ApprovalOut(ORMModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    tool_name: str
    tool_call_id: str | None = None
    #: Already redacted when the row was written — see `approvals.redact`.
    input: dict[str, Any] = Field(default_factory=dict)
    risk: ApprovalRisk
    rationale: str | None
    status: ApprovalStatus
    decided_by: uuid.UUID | None
    decided_at: datetime | None
    expires_at: datetime | None
    created_at: datetime


__all__ = ["ApprovalOut", "ApprovalResolve"]
