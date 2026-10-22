from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from app.schemas.common import ApiModel, ORMModel


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)
    name: str = Field(default="", max_length=200)
    #: The token from an invite link. Needed when sign-up is by invitation
    #: (`REGISTRATION=invite`); ignored otherwise.
    invite_token: str | None = Field(default=None, max_length=200)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenPair(ApiModel):
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
    #: When their address was shown to reach them (Phase 7b.6); null until.
    email_verified_at: datetime | None = None


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    new_password: str = Field(min_length=8, max_length=200)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=8, max_length=200)


class EmailVerify(BaseModel):
    token: str = Field(min_length=10, max_length=200)


class MembershipOut(ORMModel):
    org_id: uuid.UUID
    role: str


class LoginOut(ApiModel):
    """A sign-in's answer: the tokens, or (when the account has two-step
    sign-in, Phase 8.4) a challenge to finish with a code."""

    access_token: str | None = None
    refresh_token: str | None = None
    token_type: str = "bearer"
    expires_in: int | None = None
    two_factor_challenge: str | None = None


class TwoFactorFinish(BaseModel):
    challenge: str = Field(min_length=10, max_length=2000)
    code: str = Field(min_length=6, max_length=40)


class TwoFactorCode(BaseModel):
    code: str = Field(min_length=6, max_length=40)


class TwoFactorSetupOut(ApiModel):
    #: For typing into the app; the QR code holds the same.
    secret: str
    otpauth_uri: str


class TwoFactorConfirmed(ApiModel):
    #: Shown this once: each works once, if the phone is lost.
    recovery_codes: list[str]
    #: A fresh session that has passed the second factor.
    tokens: TokenPair


class RecoveryCodesOut(ApiModel):
    recovery_codes: list[str]


class TwoFactorStatus(ApiModel):
    enabled: bool
    recovery_codes_left: int = 0
    #: This session passed a second factor.
    session_verified: bool = False
    #: The server holds back admin powers without it, and this person is an
    #: owner or admin somewhere.
    required: bool = False


class SignInMethods(ApiModel):
    """How this account signs in (Phase 8.3)."""

    password: bool
    #: The Google account's address, when one is connected.
    google: str | None = None


class MeResponse(ApiModel):
    user: UserOut
    memberships: list[MembershipOut]
    sign_in: SignInMethods | None = None
    two_factor: TwoFactorStatus | None = None


class ProvidersOut(ApiModel):
    """The ways to sign in on this server, for the sign-in page."""

    password: bool = True
    google: bool = False
    #: The Google Workspace domain Google sign-in is limited to, if any.
    google_domain: str | None = None


class LinkTicketOut(ApiModel):
    ticket: str


class DeviceCodeOut(ApiModel):
    """A code to sign in on another device (Phase 8.6), and when it stops
    working."""

    code: str
    expires_at: datetime


class DeviceCodeIn(BaseModel):
    code: str = Field(min_length=6, max_length=20)


class GoogleExchange(BaseModel):
    code: str = Field(min_length=10, max_length=200)


__all__ = [
    "DeviceCodeIn",
    "DeviceCodeOut",
    "EmailVerify",
    "GoogleExchange",
    "LinkTicketOut",
    "LoginOut",
    "LoginRequest",
    "MeResponse",
    "MembershipOut",
    "PasswordChange",
    "PasswordResetConfirm",
    "PasswordResetRequest",
    "ProvidersOut",
    "RecoveryCodesOut",
    "RefreshRequest",
    "RegisterRequest",
    "SignInMethods",
    "TokenPair",
    "TwoFactorCode",
    "TwoFactorConfirmed",
    "TwoFactorFinish",
    "TwoFactorSetupOut",
    "TwoFactorStatus",
    "UserOut",
]
