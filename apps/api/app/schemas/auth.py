from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from app.schemas.common import ORMModel


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)
    name: str = Field(default="", max_length=200)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class UserOut(ORMModel):
    id: uuid.UUID
    email: str
    name: str
    is_active: bool
    last_login_at: datetime | None
    created_at: datetime


class MembershipOut(ORMModel):
    org_id: uuid.UUID
    role: str


class MeResponse(BaseModel):
    user: UserOut
    memberships: list[MembershipOut]


__all__ = [
    "LoginRequest",
    "MeResponse",
    "MembershipOut",
    "RefreshRequest",
    "RegisterRequest",
    "TokenPair",
    "UserOut",
]
