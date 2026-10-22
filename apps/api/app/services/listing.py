"""Who sees an assistant in Chat, and how the catalog shows it (Phase 8.5,
plan D3).

- **The draft** (`assistants.draft_listing`) is the builder's: category,
  starters, audience. Saving it changes nothing in Chat.
- **Submitting** freezes it into the version with the name and description
  (`snapshot`); the admin reviews it with the rest, and approving it is
  what puts it in front of people.
- **The audience** of the live version decides who sees and chats with
  it: everyone, members of named groups, or named people. The owner,
  admins and the assistant's builders always do: they review and test it.
  Group membership changes take effect at once: the version names the
  group, not its members.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import BadRequest
from app.models.assistant import Assistant, AssistantStatus, AssistantVersion, VersionStatus
from app.models.enums import MemberRole
from app.models.group import Group, GroupMember
from app.models.membership import Membership
from app.schemas.listing import Listing, VersionListing
from app.security.access import can_edit_assistant


def draft_of(assistant: Assistant) -> Listing:
    return Listing.model_validate(assistant.draft_listing or {})


async def save_draft(session: AsyncSession, assistant: Assistant, listing: Listing) -> Listing:
    """The builder's draft, checked: the groups and people it names are this
    organisation's."""
    aud = listing.audience
    if aud.kind == "groups":
        if not aud.group_ids:
            raise BadRequest("Choose at least one group", code="audience_empty")
        found = set(
            (
                await session.scalars(
                    select(Group.id).where(
                        Group.org_id == assistant.org_id, Group.id.in_(aud.group_ids)
                    )
                )
            ).all()
        )
        if found != set(aud.group_ids):
            raise BadRequest("A group named isn't in this organisation", code="audience_unknown")
    if aud.kind == "people":
        if not aud.user_ids:
            raise BadRequest("Choose at least one person", code="audience_empty")
        found = set(
            (
                await session.scalars(
                    select(Membership.user_id).where(
                        Membership.org_id == assistant.org_id,
                        Membership.user_id.in_(aud.user_ids),
                        Membership.deactivated_at.is_(None),
                    )
                )
            ).all()
        )
        if found != set(aud.user_ids):
            raise BadRequest("A person named isn't in this organisation", code="audience_unknown")
    # Only the kind's own list is kept.
    clean = listing.model_copy(
        update={
            "audience": aud.model_copy(
                update={
                    "group_ids": aud.group_ids if aud.kind == "groups" else [],
                    "user_ids": aud.user_ids if aud.kind == "people" else [],
                }
            )
        }
    )
    assistant.draft_listing = clean.model_dump(mode="json")
    await session.commit()
    return clean


def snapshot(assistant: Assistant) -> dict[str, Any]:
    """What a submitted version carries."""
    frozen = VersionListing(
        **draft_of(assistant).model_dump(),
        name=assistant.name,
        description=assistant.description,
    )
    return frozen.model_dump(mode="json")


def of_version(version: AssistantVersion | None, assistant: Assistant) -> VersionListing:
    """A version's listing. One from before 8.5 (none recorded) is for
    everyone, as it always was."""
    if version is None or not version.listing:
        return VersionListing(name=assistant.name, description=assistant.description)
    return VersionListing.model_validate(version.listing)


async def live_listing(session: AsyncSession, assistant: Assistant) -> VersionListing:
    version = (
        await session.get(AssistantVersion, assistant.current_version_id)
        if assistant.current_version_id
        else None
    )
    return of_version(version, assistant)


async def reaches(session: AsyncSession, listing: VersionListing, membership: Membership) -> bool:
    aud = listing.audience
    if aud.kind == "everyone":
        return True
    if aud.kind == "people":
        return membership.user_id in aud.user_ids
    found = await session.scalar(
        select(GroupMember.id)
        .where(
            GroupMember.user_id == membership.user_id,
            GroupMember.group_id.in_(aud.group_ids),
        )
        .limit(1)
    )
    return found is not None


async def can_see(session: AsyncSession, assistant: Assistant, membership: Membership) -> bool:
    """Whether this person sees the assistant in Chat and may chat with it."""
    if membership.role.satisfies(MemberRole.admin):
        return True
    if await can_edit_assistant(session, assistant, membership):
        return True
    if assistant.status is not AssistantStatus.published:
        return False
    if not await reaches(session, await live_listing(session, assistant), membership):
        return False
    # Only to people who can read all of its restricted knowledge (Phase
    # 9.4, plan D6): nobody gets a thinner version of it.
    from app.services import knowledge_access

    return await knowledge_access.reads_all(session, assistant, membership)


async def groups_in_use(session: AsyncSession, group_id: uuid.UUID) -> list[str]:
    """The assistants whose live or waiting version names this group."""
    rows = (
        await session.execute(
            select(Assistant.name, AssistantVersion.listing)
            .join(AssistantVersion, AssistantVersion.assistant_id == Assistant.id)
            .where(AssistantVersion.status.in_((VersionStatus.approved, VersionStatus.submitted)))
        )
    ).all()
    names = []
    for name, listing in rows:
        ids = ((listing or {}).get("audience") or {}).get("group_ids") or []
        if str(group_id) in {str(i) for i in ids} and name not in names:
            names.append(name)
    return names


__all__ = [
    "can_see",
    "draft_of",
    "groups_in_use",
    "live_listing",
    "of_version",
    "reaches",
    "save_draft",
    "snapshot",
]
