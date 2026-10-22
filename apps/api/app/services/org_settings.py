"""An organisation's own switches (Phase 7b.8).

Stored in `organizations.settings` as JSON, read through `Switches` so every
key has a default: a switch added later needs no migration, and a key a
newer version wrote and this one doesn't know is kept. The owner changes
them, and each change is in the audit log with its before and after.

Each switch is the safe way on by default; turning one off is a choice the
owner makes for their organisation, and is recorded.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import BadRequest, NotFound
from app.config import settings
from app.models.organization import Organization
from app.services import audit


class Switches(BaseModel):
    #: Email the admins and the owner when a version waits for review (7b.5).
    review_emails: bool = True
    #: Email whoever submitted a version when it is approved or rejected.
    decision_emails: bool = True
    #: Email whoever decides a request when it waits for them, and once more
    #: as a reminder (Phase 8.1; since 9.1 every approver request).
    approval_emails: bool = True
    #: Someone of the allowed Google Workspace domain may sign in with Google
    #: without an invite, and joins as an Employee (Phase 8.3). Off unless
    #: the owner turns it on; needs GOOGLE_ALLOWED_DOMAIN.
    google_auto_join: bool = False
    #: The only owner may approve their own version, with a note, when
    #: nobody else can review it (7b.3). Off: another reviewer is required.
    sole_reviewer_self_approval: bool = True
    #: What the Chat app shows when it can't reach the server (Phase 8.6):
    #: how to get on the office network or VPN, in the company's words. The
    #: app keeps it from its last visit, since the IT handbook sits behind
    #: the same VPN. Empty: a generic sentence only.
    offline_help: str = ""
    #: Conversations quiet for this many days are erased for good (Phase
    #: 8.10), by `services.retention`. Admins set it (`set_retention`).
    retention_days: int = 365
    #: Who to ask about your data (Phase 8.7), named in the privacy notice.
    #: Empty: the owner.
    privacy_contact: str = ""
    #: How long an approver request waits before it expires, in hours
    #: (Phase 9.1). The asker is told when it does.
    action_expiry_hours: int = 72
    #: When the approvers are reminded, once, in hours after asking. 0: never.
    action_reminder_hours: int = 24
    #: Systems on the office network or VPN assistants may reach (Phase 9.3):
    #: [{host, cidr, ports, http, note}], see `services/destinations.py`.
    internal_destinations: list[dict[str, Any]] = []
    #: What previews and eval runs may spend in a month, org-wide, in USD
    #: (Phase 9.7). None: no cap. Kept apart from the org's and the
    #: assistants' budgets, so testing never stops Chat.
    builder_monthly_usd: float | None = None


def switches_of(org: Organization) -> Switches:
    known = {k: v for k, v in (org.settings or {}).items() if k in Switches.model_fields}
    return Switches.model_validate(known)


async def switches(session: AsyncSession, org_id: uuid.UUID) -> Switches:
    org = await session.get(Organization, org_id)
    return switches_of(org) if org is not None else Switches()


def _normalize(changes: dict[str, Any], current: Switches) -> None:
    """Check and tidy the switches being set, in place. Raises BadRequest."""
    expiry = changes.get("action_expiry_hours", current.action_expiry_hours)
    reminder = changes.get("action_reminder_hours", current.action_reminder_hours)
    if reminder and reminder >= expiry:
        raise BadRequest(
            "The reminder has to come before the request expires", code="reminder_after_expiry"
        )
    if changes.get("internal_destinations") is not None:
        from app.services import destinations

        try:
            changes["internal_destinations"] = destinations.checked(
                list(changes["internal_destinations"])
            )
        except ValueError as exc:
            raise BadRequest(str(exc), code="invalid_destination") from exc
    if isinstance(changes.get("offline_help"), str):
        changes["offline_help"] = changes["offline_help"].strip()
    if isinstance(changes.get("privacy_contact"), str):
        changes["privacy_contact"] = changes["privacy_contact"].strip().lower()


async def update(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    actor_id: uuid.UUID,
    name: str | None,
    changes: dict[str, Any],
    ip: str | None,
) -> Organization:
    """Rename, and set switches. Records what changed, if anything did."""
    org = await session.get(Organization, org_id)
    if org is None:
        raise NotFound("Organization not found")
    changed: dict[str, Any] = {}
    if name is not None and name.strip() and name.strip() != org.name:
        changed["name"] = {"from": org.name, "to": name.strip()}
        org.name = name.strip()
    current = switches_of(org)
    stored = dict(org.settings or {})
    if changes.get("google_auto_join") and not current.google_auto_join:
        from app.services import google

        if not (google.enabled() and settings.google_allowed_domain):
            raise BadRequest(
                "Auto-join needs Google sign-in set up with GOOGLE_ALLOWED_DOMAIN on the server",
                code="auto_join_needs_domain",
            )
    _normalize(changes, current)
    for key, value in changes.items():
        if key not in Switches.model_fields:
            continue
        if getattr(current, key) != value:
            changed[key] = {"from": getattr(current, key), "to": value}
        stored[key] = value
    # A new dict, so the JSON column sees the change.
    org.settings = stored
    if changed:
        await audit.record(
            session,
            action="org.settings.update",
            org_id=org_id,
            actor_user_id=actor_id,
            target_type="org",
            target_id=org_id,
            meta={"changed": changed},
            ip=ip,
        )
    await session.commit()
    await session.refresh(org)
    return org


async def set_retention(
    session: AsyncSession, *, org_id: uuid.UUID, days: int, actor_id: uuid.UUID, ip: str | None
) -> Organization:
    """How long conversations are kept (Phase 8.10): an admin's, audited."""
    org = await session.get(Organization, org_id)
    if org is None:
        raise NotFound("Organization not found")
    before = switches_of(org).retention_days
    if before != days:
        org.settings = {**(org.settings or {}), "retention_days": days}
        await audit.record(
            session,
            action="org.retention.update",
            org_id=org_id,
            actor_user_id=actor_id,
            target_type="org",
            target_id=org_id,
            meta={"from": before, "to": days},
            ip=ip,
        )
    await session.commit()
    await session.refresh(org)
    return org


__all__ = ["Switches", "set_retention", "switches", "switches_of", "update"]
