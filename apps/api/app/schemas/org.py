from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from app.models.enums import MemberRole
from app.schemas.common import ORMModel


class OrgOut(ORMModel):
    id: uuid.UUID
    name: str
    slug: str
    is_personal: bool
    created_at: datetime


class OrgCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class MemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    name: str
    role: MemberRole
    joined_at: datetime


class RoleUpdate(BaseModel):
    role: MemberRole


class InviteCreate(BaseModel):
    email: EmailStr
    role: MemberRole = MemberRole.member


class InviteOut(BaseModel):
    id: uuid.UUID
    email: str
    role: MemberRole
    expires_at: datetime
    # Only returned once, at creation time.
    accept_url: str | None = None


class AuditEntryOut(ORMModel):
    id: uuid.UUID
    actor_user_id: uuid.UUID | None
    action: str
    target_type: str | None
    target_id: str | None
    meta: dict
    created_at: datetime


__all__ = [
    "AuditEntryOut",
    "InviteCreate",
    "InviteOut",
    "MemberOut",
    "OrgCreate",
    "OrgOut",
    "RoleUpdate",
]
