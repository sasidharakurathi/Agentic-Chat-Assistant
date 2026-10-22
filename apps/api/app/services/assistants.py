"""Assistant + version operations: drafts, compile/validate, the review of
versions (submit, approve, reject, roll back), pausing, diff."""

from __future__ import annotations

import secrets
import uuid
from collections.abc import Awaitable
from datetime import UTC, datetime

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import cli_files, interrupts, session_store
from app.api.errors import BadRequest, Conflict, NotFound
from app.db.pagination import PageResult, keyset_page
from app.graph.compile import compile_graph
from app.graph.nodes import Graph
from app.graph.project import project_config
from app.graph.validate import ValidationResult, validate_graph
from app.logging import get_logger
from app.models.approval import Approval, ApprovalStatus
from app.models.assistant import Assistant, AssistantStatus, AssistantVersion, VersionStatus
from app.models.conversation import Conversation, ConversationStatus
from app.models.enums import MemberRole
from app.models.integration import DbConnection, McpServer
from app.models.rag import DataSource
from app.models.secret import Secret
from app.schemas.assistant import (
    DraftConfigSaveResult,
    DraftGraphSaveResult,
    DriftItem,
    VersionDiff,
    VersionDrift,
)
from app.schemas.assistant_config import AssistantConfig, default_config
from app.services import attachments, audit, duties, manifest, notify, turns
from app.services import listing as listing_service
from app.services.diff import diff_graphs, diff_values
from app.services.graph_refs import reference_issues
from app.services.slug import slugify
from app.storage.s3 import delete_object

log = get_logger(__name__)

# ── helpers ──────────────────────────────────────────────────


def _dump(model: AssistantConfig | Graph) -> dict:
    return model.model_dump(mode="json")


async def _unique_slug(session: AsyncSession, org_id: uuid.UUID, name: str) -> str:
    base = slugify(name)[:100]
    candidate = base
    for _ in range(6):
        clash = await session.scalar(
            select(Assistant.id).where(Assistant.org_id == org_id, Assistant.slug == candidate)
        )
        if clash is None:
            return candidate
        candidate = f"{base}-{secrets.token_hex(3)}"
    return f"{base}-{secrets.token_hex(6)}"


def load_config(assistant: Assistant) -> AssistantConfig:
    return AssistantConfig.model_validate(assistant.draft_config)


def load_graph(assistant: Assistant) -> Graph:
    return Graph.model_validate(assistant.draft_graph)


def draft_validation(assistant: Assistant) -> ValidationResult:
    """Structural checks only (pure). See `full_validation` for references."""
    return validate_graph(load_graph(assistant))


async def full_validation(
    session: AsyncSession, assistant: Assistant, graph: Graph | None = None
) -> ValidationResult:
    """Structure *and* references. The structural validator is pure and cannot
    see whether a node's connection or data source actually exists, belongs
    to this assistant, or allows what the node asks of it; this adds that."""
    graph = graph if graph is not None else load_graph(assistant)
    structural = validate_graph(graph)
    references = await reference_issues(session, assistant.id, graph)
    return ValidationResult(
        errors=[*structural.errors, *references], warnings=list(structural.warnings)
    )


# ── CRUD ─────────────────────────────────────────────────────


async def create(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    name: str,
    description: str,
    ip: str | None = None,
) -> Assistant:
    config = default_config()
    graph = project_config(config)
    assistant = Assistant(
        org_id=org_id,
        name=name.strip(),
        slug=await _unique_slug(session, org_id, name),
        description=description.strip(),
        created_by=user_id,
        status=AssistantStatus.draft,
        draft_graph=_dump(graph),
        draft_config=_dump(config),
    )
    session.add(assistant)
    await session.flush()
    await audit.record(
        session,
        action="assistant.create",
        org_id=org_id,
        actor_user_id=user_id,
        target_type="assistant",
        target_id=assistant.id,
        ip=ip,
    )
    await session.commit()
    return assistant


async def get(session: AsyncSession, *, org_id: uuid.UUID, assistant_id: uuid.UUID) -> Assistant:
    assistant = await session.scalar(
        select(Assistant).where(Assistant.id == assistant_id, Assistant.org_id == org_id)
    )
    if assistant is None:
        raise NotFound("Assistant not found")
    return assistant


async def list_for_org(
    session: AsyncSession, org_id: uuid.UUID, *, limit: int = 50, cursor: str | None = None
) -> PageResult[Assistant]:
    # Most recently edited first. `updated_at` is mutable, so an assistant
    # edited while someone pages through the list jumps to page 1 and can be
    # missed on page 2. Accepted: it is back on top at the next refresh, and
    # "recently worked on first" is what this list is for.
    return await keyset_page(
        session,
        select(Assistant).where(Assistant.org_id == org_id),
        [Assistant.updated_at, Assistant.id],
        limit=limit,
        cursor=cursor,
    )


async def update_meta(
    session: AsyncSession,
    assistant: Assistant,
    *,
    name: str | None,
    description: str | None,
    actor_id: uuid.UUID,
    ip: str | None = None,
) -> Assistant:
    """Rename or redescribe. The status is not an edit any more (Phase 7b.1):
    a builder could mark an unapproved assistant published, or reopen one an
    admin had paused. Those are the lifecycle actions below."""
    changes: dict[str, dict[str, str]] = {}
    if name is not None and name.strip() != assistant.name:
        changes["name"] = {"from": assistant.name, "to": name.strip()}
        assistant.name = name.strip()
    if description is not None and description.strip() != assistant.description:
        changes["description"] = {"from": assistant.description, "to": description.strip()}
        assistant.description = description.strip()
    await session.flush()
    await audit.record(
        session,
        action="assistant.update",
        org_id=assistant.org_id,
        actor_user_id=actor_id,
        target_type="assistant",
        target_id=assistant.id,
        meta=changes,
        ip=ip,
    )
    await session.commit()
    # `updated_at` is set by the database on UPDATE, so after the commit it is
    # expired; reading it while building the response was a lazy load outside
    # the async context (MissingGreenlet, a 500 on every rename). Postgres
    # happens to return it with the UPDATE, which is why the dev stack never
    # showed it; nothing tested a rename before the role matrix did.
    await session.refresh(assistant)
    return assistant


# ── drafts ───────────────────────────────────────────────────


async def save_draft_graph(
    session: AsyncSession, assistant: Assistant, graph: Graph
) -> DraftGraphSaveResult:
    assistant.draft_graph = _dump(graph)
    config: AssistantConfig | None = None
    # Compile on *structure* alone: a bad reference must not freeze the draft
    # config (and with it the panels) — it only has to block publishing.
    if validate_graph(graph).ok:
        config = compile_graph(graph)
        assistant.draft_config = _dump(config)
    validation = await full_validation(session, assistant, graph)
    await session.flush()
    await session.commit()
    return DraftGraphSaveResult(graph=graph, validation=validation, config=config)


async def save_draft_config(
    session: AsyncSession, assistant: Assistant, config: AssistantConfig
) -> DraftConfigSaveResult:
    existing = None
    try:
        existing = load_graph(assistant)
    except Exception:
        existing = None
    graph = project_config(config, existing_graph=existing)
    assistant.draft_config = _dump(config)
    assistant.draft_graph = _dump(graph)
    await session.flush()
    validation = await full_validation(session, assistant, graph)
    await session.commit()
    return DraftConfigSaveResult(config=config, graph=graph, validation=validation)


# ── versions and their review (Phase 7b.1) ──────────────────
#
# A builder submits the draft as a new version; it answers nobody until an
# admin approves it. Approving makes it the live version (the one Chat
# answers from); only approving and an admin's rollback move that pointer.
# Each version keeps its graph and config as submitted, so what was approved
# is exactly what runs.


async def _locked(session: AsyncSession, assistant: Assistant) -> Assistant:
    """The assistant's row, locked until the commit: two reviews of one
    assistant (or a submit racing an approve) take turns."""
    locked = await session.scalar(
        select(Assistant).where(Assistant.id == assistant.id).with_for_update()
    )
    if locked is None:
        raise NotFound("Assistant not found")
    return locked


async def _references_hold(
    session: AsyncSession, assistant: Assistant, version: AssistantVersion
) -> None:
    """A version can be approved or made live only while everything it names
    still exists and allows what it asks: a knowledge base or connection
    deleted since it was submitted would otherwise fail in the middle of
    someone's conversation."""
    validation = await full_validation(session, assistant, Graph.model_validate(version.graph))
    if not validation.ok:
        raise Conflict(
            f"Version {version.version_number} names something that has changed since it "
            "was submitted; fix the draft and submit it again",
            code="version_invalid",
            details={"errors": [e.model_dump() for e in validation.errors]},
        )
    # What the admin reviewed is what goes live (plan D16): a connection,
    # MCP server or tool changed since, or a document deleted, and it is
    # not the version that was reviewed any more.
    if version.manifest is None:
        await manifest.record(session, version, AssistantConfig.model_validate(version.config))
    assert version.manifest is not None
    drift = await manifest.check(session, assistant.id, version.manifest)
    if drift:
        raise Conflict(
            f"Version {version.version_number} uses something that has changed since it was "
            f"submitted: {drift[0].describe()}. Submit the draft again to review it as it is now",
            code="version_drifted",
            details={"drift": [d.as_dict() for d in drift]},
        )


#: Warnings a builder may leave in a draft but an admin may not approve
#: (Phase 7b.1): an HTTP tool with no allowed sites refuses every request,
#: and approving it would put a broken tool in front of everyone (7a.9).
REFUSED_AT_APPROVAL = frozenset({"http_no_allowed_sites"})


async def _review_one(
    session: AsyncSession,
    version: AssistantVersion,
    *,
    expected: VersionStatus,
    to: VersionStatus,
    reviewer: uuid.UUID | None = None,
    note: str | None = None,
) -> None:
    """Move one version from `expected` to `to`, or refuse: a conditional
    UPDATE, so of two admins deciding at once only one decision lands."""
    values: dict[str, object] = {"status": to}
    if reviewer is not None:
        values |= {"reviewed_by": reviewer, "reviewed_at": datetime.now(UTC)}
    if note is not None:
        values["review_note"] = note.strip()
    result = await session.execute(
        update(AssistantVersion)
        .where(AssistantVersion.id == version.id, AssistantVersion.status == expected)
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:  # type: ignore[attr-defined]
        # Read before the rollback, which expires every loaded row.
        number = version.version_number
        await session.rollback()
        raise Conflict(
            f"Version {number} is no longer {expected.value}",
            code=f"version_not_{expected.value}",
        )
    await session.refresh(version)


async def _tell(sending: Awaitable[object]) -> None:
    """Email is a courtesy (Phase 7b.5): a failure to send is logged and
    recorded, never turned into a failed review."""
    try:
        await sending
    except Exception as exc:
        log.warning("notify_failed", error=type(exc).__name__)


async def _live_number(session: AsyncSession, assistant: Assistant) -> int | None:
    if assistant.current_version_id is None:
        return None
    return await session.scalar(
        select(AssistantVersion.version_number).where(
            AssistantVersion.id == assistant.current_version_id
        )
    )


async def submit(
    session: AsyncSession,
    assistant: Assistant,
    *,
    actor_id: uuid.UUID,
    note: str,
    ip: str | None = None,
) -> AssistantVersion:
    """Freeze the draft as a new version and put it up for review. It goes
    live only when an admin approves it; a submission still waiting is
    superseded by this one."""
    graph = load_graph(assistant)
    validation = await full_validation(session, assistant, graph)
    if not validation.ok:
        raise BadRequest(
            "The draft graph has validation errors; fix them before submitting",
            code="graph_invalid",
            details={"errors": [e.model_dump() for e in validation.errors]},
        )
    config = compile_graph(graph)
    assistant = await _locked(session, assistant)

    superseded = list(
        (
            await session.scalars(
                select(AssistantVersion.version_number).where(
                    AssistantVersion.assistant_id == assistant.id,
                    AssistantVersion.status == VersionStatus.submitted,
                )
            )
        ).all()
    )
    if superseded:
        await session.execute(
            update(AssistantVersion)
            .where(
                AssistantVersion.assistant_id == assistant.id,
                AssistantVersion.status == VersionStatus.submitted,
            )
            .values(status=VersionStatus.superseded)
            .execution_options(synchronize_session=False)
        )
    highest = await session.scalar(
        select(func.max(AssistantVersion.version_number)).where(
            AssistantVersion.assistant_id == assistant.id
        )
    )
    version = AssistantVersion(
        assistant_id=assistant.id,
        org_id=assistant.org_id,
        version_number=(highest or 0) + 1,
        graph=_dump(graph),
        config=_dump(config),
        created_by=actor_id,
        note=note.strip(),
        status=VersionStatus.submitted,
        # How Chat will show it, and to whom (Phase 8.5): reviewed with it.
        listing=listing_service.snapshot(assistant),
    )
    session.add(version)
    await session.flush()
    # Frozen with it: the knowledge, connections and tools it uses, as they
    # are now (plan D16).
    await manifest.record(session, version, config)

    assistant.draft_config = _dump(config)  # keep the draft canonical
    await audit.record(
        session,
        action="assistant.version.submit",
        org_id=assistant.org_id,
        actor_user_id=actor_id,
        target_type="assistant_version",
        target_id=version.id,
        meta={
            "version": version.version_number,
            "superseded": superseded,
            "manifest": version.manifest_hash,
        },
        ip=ip,
    )
    await session.commit()
    await _tell(notify.version_submitted(session, assistant, version))
    return version


async def approve(
    session: AsyncSession,
    assistant: Assistant,
    number: int,
    *,
    actor_id: uuid.UUID,
    note: str,
    actor_role: MemberRole = MemberRole.admin,
    ip: str | None = None,
) -> AssistantVersion:
    """An admin approves the submitted version: it becomes the live one.
    Conversations already running stay on the version they started on.
    Not their own (Phase 7b.3): whoever submitted it or built the assistant
    needs someone else, unless they are the owner with nobody to ask."""
    assistant = await _locked(session, assistant)
    version = await get_version(session, assistant.id, number)
    if version.status is not VersionStatus.submitted:
        raise Conflict(
            f"Version {number} is {version.status.value}, not waiting for review",
            code="version_not_submitted",
        )
    review = await duties.may_approve_version(
        session,
        org_id=assistant.org_id,
        approver_id=actor_id,
        approver_role=actor_role,
        submitted_by=version.created_by,
        built_by=assistant.created_by,
        note=note,
    )
    await _references_hold(session, assistant, version)
    refused = [
        w
        for w in validate_graph(Graph.model_validate(version.graph)).warnings
        if w.code in REFUSED_AT_APPROVAL
    ]
    if refused:
        raise Conflict(
            f"{refused[0].message} Version {number} can't be approved until it does",
            code="version_not_approvable",
            details={"warnings": [w.model_dump() for w in refused]},
        )
    previous = await _live_number(session, assistant)
    await _review_one(
        session,
        version,
        expected=VersionStatus.submitted,
        to=VersionStatus.approved,
        reviewer=actor_id,
        note=note,
    )
    assistant.current_version_id = version.id
    if assistant.status is AssistantStatus.draft:
        # A paused assistant stays paused: approving is not reopening.
        assistant.status = AssistantStatus.published
    await audit.record(
        session,
        action="assistant.version.approve",
        org_id=assistant.org_id,
        actor_user_id=actor_id,
        target_type="assistant_version",
        target_id=version.id,
        meta={
            "version": number,
            "previous_live": previous,
            "note": note.strip(),
            "manifest": version.manifest_hash,
            # Whose work it was, and whether the approver was one of them.
            "submitted_by": str(version.created_by) if version.created_by else None,
            "built_by": str(assistant.created_by) if assistant.created_by else None,
            "approver_role": actor_role.value,
            "self_approved": review.self_approved,
        },
        ip=ip,
    )
    await session.commit()
    await _tell(
        notify.version_reviewed(session, assistant, version, approved=True, reviewer_id=actor_id)
    )
    return version


async def reject(
    session: AsyncSession,
    assistant: Assistant,
    number: int,
    *,
    actor_id: uuid.UUID,
    note: str,
    ip: str | None = None,
) -> AssistantVersion:
    """An admin turns the submitted version down, saying why."""
    version = await get_version(session, assistant.id, number)
    await _review_one(
        session,
        version,
        expected=VersionStatus.submitted,
        to=VersionStatus.rejected,
        reviewer=actor_id,
        note=note,
    )
    await audit.record(
        session,
        action="assistant.version.reject",
        org_id=assistant.org_id,
        actor_user_id=actor_id,
        target_type="assistant_version",
        target_id=version.id,
        meta={"version": number, "note": note.strip()},
        ip=ip,
    )
    await session.commit()
    await _tell(
        notify.version_reviewed(session, assistant, version, approved=False, reviewer_id=actor_id)
    )
    return version


async def withdraw(
    session: AsyncSession,
    assistant: Assistant,
    number: int,
    *,
    actor_id: uuid.UUID,
    ip: str | None = None,
) -> AssistantVersion:
    """The builder takes back a submission nobody has reviewed yet."""
    version = await get_version(session, assistant.id, number)
    await _review_one(
        session, version, expected=VersionStatus.submitted, to=VersionStatus.withdrawn
    )
    await audit.record(
        session,
        action="assistant.version.withdraw",
        org_id=assistant.org_id,
        actor_user_id=actor_id,
        target_type="assistant_version",
        target_id=version.id,
        meta={"version": number},
        ip=ip,
    )
    await session.commit()
    return version


async def make_live(
    session: AsyncSession,
    assistant: Assistant,
    number: int,
    *,
    actor_id: uuid.UUID,
    ip: str | None = None,
) -> AssistantVersion:
    """Roll back: an admin makes an earlier approved version the live one,
    without rebuilding anything from the draft. The version it replaces is
    marked rolled back: it cannot go live again without a new submission,
    and its conversations move to this one on their next message."""
    assistant = await _locked(session, assistant)
    version = await get_version(session, assistant.id, number)
    if version.status is not VersionStatus.approved:
        raise Conflict(
            f"Version {number} is {version.status.value}; only an approved version can go live",
            code="version_not_approved",
        )
    if version.id == assistant.current_version_id:
        raise Conflict(f"Version {number} is already live", code="version_already_live")
    await _references_hold(session, assistant, version)
    replaced = (
        await session.get(AssistantVersion, assistant.current_version_id)
        if assistant.current_version_id is not None
        else None
    )
    if replaced is not None:
        await _review_one(
            session, replaced, expected=VersionStatus.approved, to=VersionStatus.rolled_back
        )
    assistant.current_version_id = version.id
    await audit.record(
        session,
        action="assistant.version.rollback",
        org_id=assistant.org_id,
        actor_user_id=actor_id,
        target_type="assistant_version",
        target_id=version.id,
        meta={
            "version": number,
            "from": replaced.version_number if replaced is not None else None,
        },
        ip=ip,
    )
    await session.commit()
    return version


# ── pausing (Phase 7b.1) ─────────────────────────────────────


async def archive(
    session: AsyncSession,
    assistant: Assistant,
    *,
    actor_id: uuid.UUID,
    reason: str,
    ip: str | None = None,
) -> Assistant:
    """Pause: an admin stops the assistant answering anyone, at once. The
    approvals its conversations are waiting on expire, and answers being
    written are stopped. Its versions and conversations stay; reopening
    brings back the live version as it was."""
    if assistant.status is AssistantStatus.archived:
        raise Conflict("This assistant is already paused", code="assistant_archived")
    before = assistant.status
    assistant.status = AssistantStatus.archived
    conversation_ids = list(
        (
            await session.scalars(
                select(Conversation.id).where(
                    Conversation.assistant_id == assistant.id,
                    Conversation.status == ConversationStatus.active,
                )
            )
        ).all()
    )
    # What waits on it can't run while it is paused (the executor would
    # refuse it anyway): ended now, the sealed input wiped (Phase 9.1).
    expired = await session.execute(
        update(Approval)
        .where(
            Approval.assistant_id == assistant.id,
            Approval.status == ApprovalStatus.pending,
        )
        .values(
            status=ApprovalStatus.expired,
            decided_at=datetime.now(UTC),
            sealed_ciphertext=None,
            sealed_dek=None,
            sealed_nonce=None,
        )
        .execution_options(synchronize_session=False)
    )
    await audit.record(
        session,
        action="assistant.archive",
        org_id=assistant.org_id,
        actor_user_id=actor_id,
        target_type="assistant",
        target_id=assistant.id,
        meta={
            "from": before.value,
            "reason": reason.strip(),
            "approvals_expired": expired.rowcount,  # type: ignore[attr-defined]
        },
        ip=ip,
    )
    await session.commit()
    # After the commit, so a turn that looks again finds it paused.
    for cid in await turns.running_among(conversation_ids):
        interrupts.interrupt_local(cid)
        await interrupts.publish(cid)
    await session.refresh(assistant)
    return assistant


async def unarchive(
    session: AsyncSession,
    assistant: Assistant,
    *,
    actor_id: uuid.UUID,
    ip: str | None = None,
) -> Assistant:
    """Reopen a paused assistant: its live version answers again, or, if
    none was ever approved, it is a draft again."""
    if assistant.status is not AssistantStatus.archived:
        raise Conflict("This assistant is not paused", code="assistant_not_archived")
    assistant.status = (
        AssistantStatus.published
        if assistant.current_version_id is not None
        else AssistantStatus.draft
    )
    await audit.record(
        session,
        action="assistant.unarchive",
        org_id=assistant.org_id,
        actor_user_id=actor_id,
        target_type="assistant",
        target_id=assistant.id,
        meta={"to": assistant.status.value},
        ip=ip,
    )
    await session.commit()
    await session.refresh(assistant)
    return assistant


async def list_versions(
    session: AsyncSession, assistant_id: uuid.UUID, *, limit: int = 50, cursor: str | None = None
) -> PageResult[AssistantVersion]:
    # version_number is unique per assistant, so it is a total order alone.
    return await keyset_page(
        session,
        select(AssistantVersion).where(AssistantVersion.assistant_id == assistant_id),
        [AssistantVersion.version_number],
        limit=limit,
        cursor=cursor,
    )


async def get_version(
    session: AsyncSession, assistant_id: uuid.UUID, number: int
) -> AssistantVersion:
    version = await session.scalar(
        select(AssistantVersion).where(
            AssistantVersion.assistant_id == assistant_id,
            AssistantVersion.version_number == number,
        )
    )
    if version is None:
        raise NotFound(f"Version {number} not found")
    return version


async def drift(session: AsyncSession, assistant: Assistant, number: int) -> VersionDrift:
    """What has changed since a version was submitted. A version from before
    the gate has no manifest, and so nothing to compare."""
    version = await get_version(session, assistant.id, number)
    found = (
        await manifest.check(session, assistant.id, version.manifest)
        if version.manifest is not None
        else []
    )
    return VersionDrift(
        version=number,
        manifest_hash=version.manifest_hash,
        drift=[DriftItem.model_validate(d.as_dict()) for d in found],
    )


async def diff_versions(
    session: AsyncSession, assistant_id: uuid.UUID, a: int, b: int
) -> VersionDiff:
    va = await get_version(session, assistant_id, a)
    vb = await get_version(session, assistant_id, b)
    return VersionDiff(
        from_version=a,
        to_version=b,
        config_diff=diff_values(va.config, vb.config),
        graph_diff=diff_graphs(va.graph, vb.graph),
    )


async def delete(
    session: AsyncSession,
    assistant: Assistant,
    *,
    actor_user_id: uuid.UUID,
    ip: str | None = None,
) -> None:
    """Delete an assistant and everything it owns (task 1.2 / plan §10).

    The database cascade takes versions, conversations (with their messages,
    runs and approvals), data sources, documents, chunks and connections.
    Three things it cannot reach are handled here:

    - **sealed credentials** — `db_connections -> secrets` and
      `mcp_servers -> secrets` are SET NULL the other way round, so the
      cascade alone would orphan every ciphertext. Deleted in the same
      commit.
    - **uploaded files, session cache, scratch dirs** — outside Postgres.
      Removed *after* the commit, best-effort: a storage outage then leaves
      an orphaned file (logged), never rows pointing at deleted files.

    Usage history deliberately survives, with the assistant reference nulled.
    """
    assistant_id = assistant.id
    connections = (
        await session.scalars(select(DbConnection).where(DbConnection.assistant_id == assistant_id))
    ).all()
    servers = (
        await session.scalars(select(McpServer).where(McpServer.assistant_id == assistant_id))
    ).all()
    secret_ids = [
        ref for c in connections for ref in (c.secret_ref, c.uri_secret_ref) if ref is not None
    ] + [
        ref for m in servers for ref in (m.headers_secret_ref, m.env_secret_ref) if ref is not None
    ]
    object_keys = [
        key
        for key in (
            await session.scalars(
                select(DataSource.object_key).where(DataSource.assistant_id == assistant_id)
            )
        ).all()
        if key
    ]
    conversation_ids = list(
        (
            await session.scalars(
                select(Conversation.id).where(Conversation.assistant_id == assistant_id)
            )
        ).all()
    )
    # Screenshots sent in its conversations (Phase 9.6), deleted with them.
    image_keys = await attachments.keys_of(session, conversation_ids)

    await audit.record(
        session,
        action="assistant.delete",
        org_id=assistant.org_id,
        actor_user_id=actor_user_id,
        target_type="assistant",
        target_id=assistant_id,
        meta={
            "name": assistant.name,
            "connections": len(connections),
            "files": len(object_keys),
            "conversations": len(conversation_ids),
        },
        ip=ip,
    )
    await session.delete(assistant)
    await session.flush()
    if secret_ids:
        await session.execute(sa_delete(Secret).where(Secret.id.in_(secret_ids)))
    await session.commit()

    for key in object_keys + image_keys:
        try:
            await delete_object(key)
        except Exception as exc:
            log.warning("assistant_delete_object_failed", key=key, error=str(exc)[:200])
    for conversation_id in conversation_ids:
        await session_store.delete(conversation_id)
        cli_files.discard(conversation_id)


__all__ = [
    "approve",
    "archive",
    "create",
    "delete",
    "diff_versions",
    "draft_validation",
    "drift",
    "full_validation",
    "get",
    "get_version",
    "list_for_org",
    "list_versions",
    "load_config",
    "load_graph",
    "make_live",
    "reject",
    "save_draft_config",
    "save_draft_graph",
    "submit",
    "unarchive",
    "update_meta",
    "withdraw",
]
