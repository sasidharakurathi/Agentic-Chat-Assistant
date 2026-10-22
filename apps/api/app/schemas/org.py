from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field

from app.models.enums import MemberRole
from app.schemas.common import ApiModel, ORMModel

#: The Chat app's offline steps (Phase 8.6): a short note, not a manual.
OFFLINE_HELP_MAX = 1000


class OrgOut(ORMModel):
    id: uuid.UUID
    name: str
    slug: str
    is_personal: bool
    created_at: datetime


class OrgCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class DestinationOut(ApiModel):
    """One internal destination (Phase 9.3)."""

    host: str
    cidr: str
    ports: list[int]
    http: bool = False
    note: str = ""


class OrgSettingsOut(ApiModel):
    """The organisation's name and switches (Phase 7b.8)."""

    name: str
    review_emails: bool
    decision_emails: bool
    approval_emails: bool
    google_auto_join: bool
    sole_reviewer_self_approval: bool
    offline_help: str = ""
    privacy_contact: str = ""
    #: Phase 8.10: days a quiet conversation is kept before it is erased.
    retention_days: int = 365
    #: Phase 9.1: hours an approver request waits before it expires, and
    #: after which the approvers are reminded once (0: no reminder).
    action_expiry_hours: int = 72
    action_reminder_hours: int = 24
    #: Phase 9.3: systems on the office network or VPN assistants may reach.
    internal_destinations: list[DestinationOut] = Field(default_factory=list)
    #: Phase 9.7: what previews and eval runs may spend a month; null: no cap.
    builder_monthly_usd: float | None = None
    #: The server's mode, set by its operator: `single` is one company org.
    org_mode: Literal["single", "multi"]
    #: Whether the caller may change them: the owner.
    can_edit: bool


class OrgSettingsUpdate(BaseModel):
    """Only what is sent changes."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    review_emails: bool | None = None
    decision_emails: bool | None = None
    approval_emails: bool | None = None
    google_auto_join: bool | None = None
    sole_reviewer_self_approval: bool | None = None
    #: Phase 8.6: shown by the Chat app when it can't reach the server.
    offline_help: str | None = Field(default=None, max_length=OFFLINE_HELP_MAX)
    #: Phase 8.7: who to ask about your data; empty for the owner.
    privacy_contact: EmailStr | Literal[""] | None = None
    #: Phase 9.1: an hour to 30 days; the reminder before the expiry, or 0.
    action_expiry_hours: int | None = Field(default=None, ge=1, le=720)
    action_reminder_hours: int | None = Field(default=None, ge=0, le=719)
    #: Phase 9.3: the whole list, replacing what was there (the owner's).
    internal_destinations: list[dict[str, Any]] | None = Field(default=None, max_length=50)
    #: Phase 9.7: previews' and eval runs' monthly cap in USD. Sent as null,
    #: it removes the cap (the route keeps an explicit null).
    builder_monthly_usd: float | None = Field(default=None, ge=0.01, le=10_000_000)


class MemberOut(ApiModel):
    user_id: uuid.UUID
    email: str
    name: str
    role: MemberRole
    joined_at: datetime
    #: False when the person can't use the org: removed from it (Phase 7b.4)
    #: or their account is switched off. They count for nothing, reviewing
    #: included (7b.3).
    is_active: bool = True
    deactivated_at: datetime | None = None


class InvitePreview(ApiModel):
    """What an invite link is for, shown before anyone signs in. Only the
    holder of the (unguessable, single-use) token can ask."""

    org_name: str
    email: str
    role: MemberRole
    expires_at: datetime
    #: `revoked`: withdrawn, replaced by a newer invite, or its sender can no
    #: longer invite (Phase 7b.4).
    status: Literal["pending", "accepted", "expired", "revoked"]


class InviteAccepted(ApiModel):
    org_id: uuid.UUID
    org_name: str
    role: MemberRole


class RoleUpdate(BaseModel):
    role: MemberRole


class InviteCreate(BaseModel):
    email: EmailStr
    #: Employee by default (Phase 8.1, plan D2): most people invited chat;
    #: making someone a builder is a choice.
    role: MemberRole = MemberRole.employee


class InviteOut(ApiModel):
    id: uuid.UUID
    email: str
    role: MemberRole
    expires_at: datetime
    # Only returned once, at creation time.
    accept_url: str | None = None
    invited_by: uuid.UUID | None = None
    created_at: datetime | None = None
    #: The link was also emailed to them (Phase 7b.5); it is shown either way.
    emailed: bool = False


class MailStatusOut(ApiModel):
    """How this server sends email (Phase 7b.5). Read-only: the settings
    live in the server's environment. No username or password."""

    transport: Literal["smtp", "console", "memory", "off"]
    from_address: str | None
    host: str | None
    #: Sent and failed in the last 7 days, for the org.
    sent_7d: int
    failed_7d: int


class MailTestOut(ApiModel):
    queued: bool
    to: str


class DeactivateRequest(BaseModel):
    #: Who takes over the assistants they created; the admin doing it by
    #: default (Phase 7b.4).
    transfer_to: uuid.UUID | None = None


class NamedRef(ApiModel):
    id: str | None = None
    name: str
    assistant: str | None = None


class LeavingOut(ApiModel):
    """What deactivating someone hands on, closes or revokes (7b.4)."""

    assistants: list[NamedRef]
    #: On the assistants they built or edited: credentials they may know.
    connections: list[NamedRef]
    mcp_servers: list[NamedRef]
    pending_approvals: int
    open_invites: int
    editor_of: list[NamedRef]


class AuditEntryOut(ORMModel):
    id: uuid.UUID
    actor_user_id: uuid.UUID | None
    #: Who acted, as they were then (their email); kept when the account goes.
    actor_label: str | None = None
    action: str
    target_type: str | None
    target_id: str | None
    meta: dict
    ip: str | None = None
    created_at: datetime
    #: Its place in the hash chain (Phase 7b.7); null until the next seal.
    seq: int | None = None


class AuditIntegrityOut(ApiModel):
    """The hash chain, checked from the start (Phase 7b.7)."""

    sealed: int
    #: Written since the last seal (the worker seals every minute).
    unsealed: int
    intact: bool
    #: The first entry whose hash doesn't match, when one doesn't.
    broken_at: int | None
    #: The newest hash: copied somewhere safe, it proves later that nothing
    #: before it changed.
    last_hash: str | None


__all__ = [
    "AuditEntryOut",
    "AuditIntegrityOut",
    "InviteAccepted",
    "InviteCreate",
    "InviteOut",
    "InvitePreview",
    "MemberOut",
    "OrgCreate",
    "OrgOut",
    "OrgSettingsOut",
    "OrgSettingsUpdate",
    "RoleUpdate",
]
