"""Who may read each knowledge source (Phase 9.4, plan D6).

Each source is either **everyone's** or readable by **groups**. Deny by
default: a source with no groups that isn't everyone's reaches nobody, and
a deleted group's id simply matches no one (an admin reassigns it). Sources
from before 9.4 are everyone's, as they were.

- **Retrieval and citations** use only the sources the person asking may
  read. The people who edit the assistant (its creator, co-editors, admins
  and the owner) read all of its sources: they put them there.
- **An assistant is only offered** to people who can read every restricted
  source its live version uses (`reads_all`, in `listing.can_see`), so in
  Chat the filter is a second wall, not a different answer per person.
- **Revocation.** Whenever someone may read less (a member removed from a
  group, a group deleted, a source's readers changed), the organisation's
  access epoch goes up. A conversation on restricted knowledge last
  answered at an older epoch starts fresh: no resumed session, no summary
  or earlier messages replayed, no carried-over citations (`for_turn`).
  The memory tool is off on assistants with restricted knowledge, since a
  note can outlive its reader's access.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assistant import Assistant, AssistantVersion
from app.models.group import GroupMember
from app.models.membership import Membership
from app.models.organization import Organization
from app.models.rag import DataSource
from app.schemas.assistant_config import AssistantConfig


def readable(everyone: bool, group_ids: Iterable[str], groups: set[str]) -> bool:
    return everyone or bool({str(g) for g in group_ids} & groups)


async def groups_of(session: AsyncSession, user_id: uuid.UUID) -> set[str]:
    rows = await session.scalars(select(GroupMember.group_id).where(GroupMember.user_id == user_id))
    return {str(g) for g in rows.all()}


async def _sources(
    session: AsyncSession, assistant_id: uuid.UUID, ids: Iterable[uuid.UUID] | None = None
) -> dict[uuid.UUID, tuple[bool, list[str]]]:
    query = select(DataSource.id, DataSource.everyone, DataSource.group_ids).where(
        DataSource.assistant_id == assistant_id
    )
    if ids is not None:
        query = query.where(DataSource.id.in_(list(ids)))
    return {i: (e, list(g or [])) for i, e, g in (await session.execute(query)).all()}


async def readable_ids(
    session: AsyncSession, assistant: Assistant, membership: Membership | None
) -> set[uuid.UUID] | None:
    """The assistant's sources this person may read; None means all of them
    (they edit the assistant)."""
    from app.security.access import can_edit_assistant

    if membership is None:
        return set()
    if await can_edit_assistant(session, assistant, membership):
        return None
    groups = await groups_of(session, membership.user_id)
    return {
        i
        for i, (everyone, group_ids) in (await _sources(session, assistant.id)).items()
        if readable(everyone, group_ids, groups)
    }


async def reads_all(session: AsyncSession, assistant: Assistant, membership: Membership) -> bool:
    """Whether this person can read every restricted source the assistant's
    live version answers from (the D6 visibility rule)."""
    if assistant.current_version_id is None:
        return True
    version = await session.get(AssistantVersion, assistant.current_version_id)
    if version is None or not (version.config or {}).get("rag", {}).get("enabled"):
        return True
    named = (version.manifest or {}).get("knowledge", {}).get("sources", [])
    ids = [uuid.UUID(s["id"]) for s in named if s.get("id")]
    restricted = [g for e, g in (await _sources(session, assistant.id, ids)).values() if not e]
    if not restricted:
        return True
    groups = await groups_of(session, membership.user_id)
    return all(readable(False, g, groups) for g in restricted)


async def reads_version(
    session: AsyncSession,
    assistant: Assistant,
    version_id: uuid.UUID | None,
    allowed: set[uuid.UUID],
) -> bool:
    """Whether a reader may read every restricted source a version answered
    from (its manifest); with no version, every restricted source of the
    assistant."""
    version = await session.get(AssistantVersion, version_id) if version_id else None
    if version is not None:
        if not (version.config or {}).get("rag", {}).get("enabled"):
            return True
        named = (version.manifest or {}).get("knowledge", {}).get("sources", [])
        ids: list[uuid.UUID] | None = [uuid.UUID(s["id"]) for s in named if s.get("id")]
    else:
        ids = None
    restricted = [i for i, (e, _g) in (await _sources(session, assistant.id, ids)).items() if not e]
    return all(i in allowed for i in restricted)


@dataclass(frozen=True)
class TurnAccess:
    """What one turn may use, for the person asking."""

    config: AssistantConfig
    #: The sources they may read; None means all (an editor).
    readable: set[uuid.UUID] | None
    #: Whether the knowledge it answers from includes a restricted source.
    restricted: bool
    #: The organisation's epoch now.
    epoch: int

    def fresh_start(self, conversation_epoch: int) -> bool:
        """Whether this conversation must start over (lost access since)."""
        return self.restricted and conversation_epoch != self.epoch


async def for_turn(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    assistant_id: uuid.UUID,
    asker_id: uuid.UUID | None,
    config: AssistantConfig,
) -> TurnAccess:
    """The turn's config narrowed to what the asker may read."""
    org = await session.get(Organization, org_id)
    epoch = org.access_epoch if org is not None else 0
    if not config.rag.enabled:
        return TurnAccess(config, None, False, epoch)
    assistant = await session.get(Assistant, assistant_id)
    membership = (
        await session.scalar(
            select(Membership).where(Membership.org_id == org_id, Membership.user_id == asker_id)
        )
        if asker_id is not None
        else None
    )
    allowed = await readable_ids(session, assistant, membership) if assistant else set()
    sources = await _sources(session, assistant_id)
    in_use = [uuid.UUID(s) for s in config.rag.source_ids] or list(sources)
    restricted = any(not sources[i][0] for i in in_use if i in sources)
    cfg = config.model_copy(deep=True)
    if allowed is not None:
        keep = [i for i in in_use if i in allowed]
        if keep:
            cfg.rag.source_ids = sorted(str(i) for i in keep)
        else:
            # An empty list would mean every source.
            cfg.rag.enabled = False
    if restricted:
        cfg.memory.memory_tool = False
    return TurnAccess(cfg, allowed, restricted, epoch)


async def bump_epoch(session: AsyncSession, org_id: uuid.UUID) -> None:
    """Someone may read less: open conversations on restricted knowledge
    start fresh at their next message. The caller commits."""
    await session.execute(
        update(Organization)
        .where(Organization.id == org_id)
        .values(access_epoch=Organization.access_epoch + 1)
        .execution_options(synchronize_session=False)
    )


#: Tools whose output is passages (or names) of the knowledge base.
KB_TOOLS = ("kb_search", "kb_list_sources")
HIDDEN_RESULT = "(Passages from knowledge you can no longer read are hidden.)"


def visible_blocks(
    blocks: list[Any], allowed: set[uuid.UUID] | None, *, lost: bool = False
) -> list[Any]:
    """A message's blocks without citations of sources the reader may not
    read (an earlier answer, before they lost access). With `lost` (the
    assistant has sources they can't read, `lost_any`), knowledge search
    results go too: they hold the passages themselves (Phase 9.9)."""
    if allowed is None:
        return blocks
    keep = {str(i) for i in allowed}
    out = []
    for b in blocks:
        if not isinstance(b, dict):
            out.append(b)
            continue
        if b.get("type") == "citation" and str(b.get("data_source_id") or "") not in keep:
            continue
        name = str(b.get("name") or "")
        if lost and b.get("type", "tool_call") == "tool_call" and name.endswith(KB_TOOLS):
            out.append({**b, "output": HIDDEN_RESULT})
        else:
            out.append(b)
    return out


HIDDEN_ANSWER = (
    "This answer used knowledge you can no longer read, so it's hidden. Ask again to get "
    "an answer from what you can read now."
)


def hides_answer(blocks: list[Any], allowed: set[uuid.UUID] | None) -> bool:
    """Whether an earlier answer cited a source this reader can no longer
    read (Phase 9.9): its text is hidden with its citations, since the
    answer itself was written from that source."""
    if allowed is None:
        return False
    return any(i not in allowed for i in cited_sources([blocks]))


def shown(message: Any, allowed: set[uuid.UUID] | None, lost: bool) -> dict[str, Any]:
    """What a reader is shown of a stored message: its blocks without what
    they lost, and its text hidden when it cited a source they lost."""
    blocks = list(message.blocks or [])
    out: dict[str, Any] = {"blocks": visible_blocks(blocks, allowed, lost=lost)}
    if hides_answer(blocks, allowed):
        out["content"] = HIDDEN_ANSWER
    return out


async def lost_any(
    session: AsyncSession, assistant_id: uuid.UUID, allowed: set[uuid.UUID] | None
) -> bool:
    """Whether the assistant has a source this reader can't read."""
    if allowed is None:
        return False
    return any(i not in allowed for i in await _sources(session, assistant_id))


def cited_sources(blocks_of_messages: Iterable[list[Any]]) -> set[uuid.UUID]:
    out: set[uuid.UUID] = set()
    for blocks in blocks_of_messages:
        for b in blocks or []:
            if isinstance(b, dict) and b.get("type") == "citation" and b.get("data_source_id"):
                try:
                    out.add(uuid.UUID(str(b["data_source_id"])))
                except ValueError:
                    continue
    return out


__all__ = [
    "TurnAccess",
    "bump_epoch",
    "cited_sources",
    "for_turn",
    "groups_of",
    "hides_answer",
    "lost_any",
    "readable",
    "readable_ids",
    "reads_all",
    "reads_version",
    "shown",
    "visible_blocks",
]
