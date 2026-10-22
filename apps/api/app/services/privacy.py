"""How Chat uses your data (Phase 8.7, plans D5 and D18).

Before their first message in Chat, everyone reads one screen: what is
kept, that admins can open conversations (each opening recorded, with its
reason), what an assistant may do as them, the rules for using it, and
where messages go. They accept it, and the acceptance is recorded, so the
rules can be applied. (It isn't what makes processing lawful: work use
doesn't depend on it.)

- **The text** is the organisation's own: the built-in one below until the
  owner edits it. Every save is kept (`PrivacyNotice.revision`); a real
  change starts a new `version`, which everyone accepts again before their
  next message; a typo fixed doesn't.
- **Where messages go** is never typed in: it is read from how this server
  is set up (`processors`), so it can't fall behind it. What one assistant
  sends out is on its card (`about`).
- **Enforced by the server**: a live conversation refuses a message until
  the current version is accepted (`notice_required`). Previews and evals
  don't ask.
- **Admins' reads, shown to the person read** (`reads_of`): when, who and
  the reason, from the records 7b.2 already writes.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import Conflict
from app.config import settings
from app.db.pagination import PageResult, keyset_page
from app.models.assistant import Assistant, AssistantVersion
from app.models.audit_log import AuditLog
from app.models.conversation import Conversation
from app.models.enums import MemberRole
from app.models.integration import HttpConnection, McpServer
from app.models.membership import Membership
from app.models.privacy import NoticeAcceptance, PrivacyNotice
from app.models.user import User
from app.schemas.assistant_config import AssistantConfig
from app.services import audit, conversation_reads, org_settings

#: The longest notice the owner can save: a screen, not a policy manual.
BODY_MAX = 8000

DEFAULT_BODY = """\
Chat answers your questions with assistants your company has set up. \
Please read this before your first message.

What is kept
- Your conversations are kept on your company's server.
- Admins and the owner can open any conversation. Each time they do, they \
have to give a reason, and it is recorded. You can see each one under \
Privacy & help.
- What you use is recorded per person, to keep spending in check.
- Feedback you send on an answer (a thumbs, a comment, or "Did this \
solve it?") goes to the assistant's team with that question and answer, \
without your name.

What an assistant can do as you
- Some assistants look things up in company systems as you. Some can ask \
to change something; an admin approves each change first. Each \
assistant's card says what it uses and what leaves the building.

Please
- Never type a password, a code or any other credential, even to the IT \
assistant.
- Don't share other people's personal details unless your work needs it.
- Answers can be wrong. Check them before you act on them.

Why you're asked to accept: so these rules can be applied. Using Chat for \
your work doesn't depend on it."""


@dataclass(frozen=True)
class Processor:
    """Someone outside the building who receives data, and what."""

    name: str
    what: str


def processors() -> list[Processor]:
    """Where messages go, read from this server's settings (never stored,
    so it can't fall behind them)."""
    out = [
        Processor(
            "Anthropic (United States)",
            "Writes the answers: each message, the conversation so far, and what the "
            "assistant looks up for it.",
        )
    ]
    if settings.voyage_api_key and not settings.rag_offline:
        out.append(
            Processor(
                "Voyage AI",
                "Indexes documents and questions for the assistants that search a knowledge base.",
            )
        )
    if settings.langfuse_host:
        host = urlparse(settings.langfuse_host).hostname or settings.langfuse_host
        out.append(
            Processor(
                f"Langfuse ({host})",
                "Keeps a trace of each answer, for troubleshooting.",
            )
        )
    if settings.google_client_id:
        out.append(Processor("Google", "Only who you are, if you sign in with Google."))
    if settings.smtp_host:
        out.append(
            Processor(
                f"Email ({settings.smtp_host})",
                "Invites and notifications. They never carry what you wrote.",
            )
        )
    return out


# ── the notice ───────────────────────────────────────────────


@dataclass(frozen=True)
class Notice:
    version: int
    revision: int
    body: str
    updated_at: datetime | None
    contact: str
    custom: bool


async def _latest(session: AsyncSession, org_id: uuid.UUID) -> PrivacyNotice | None:
    return await session.scalar(
        select(PrivacyNotice)
        .where(PrivacyNotice.org_id == org_id)
        .order_by(PrivacyNotice.revision.desc())
        .limit(1)
    )


async def contact(session: AsyncSession, org_id: uuid.UUID) -> str:
    """Who to ask about your data: the organisation's privacy contact, or
    its owner."""
    switches = await org_settings.switches(session, org_id)
    if switches.privacy_contact:
        return switches.privacy_contact
    email = await session.scalar(
        select(User.email)
        .join(Membership, Membership.user_id == User.id)
        .where(
            Membership.org_id == org_id,
            Membership.role == MemberRole.owner,
            Membership.deactivated_at.is_(None),
        )
        .order_by(Membership.created_at)
        .limit(1)
    )
    return email or ""


async def current(session: AsyncSession, org_id: uuid.UUID) -> Notice:
    """The notice as it stands: the owner's latest text, or the built-in
    one as version 1."""
    row = await _latest(session, org_id)
    return Notice(
        version=row.version if row else 1,
        revision=row.revision if row else 0,
        body=row.body if row else DEFAULT_BODY,
        updated_at=row.created_at if row else None,
        contact=await contact(session, org_id),
        custom=row is not None,
    )


async def accepted_at(
    session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, version: int
) -> datetime | None:
    return await session.scalar(
        select(NoticeAcceptance.accepted_at).where(
            NoticeAcceptance.org_id == org_id,
            NoticeAcceptance.user_id == user_id,
            NoticeAcceptance.version == version,
        )
    )


async def has_accepted(session: AsyncSession, membership: Membership) -> bool:
    """Whether this person accepted the version in force. Always yes when
    the server doesn't ask (`PRIVACY_NOTICE_REQUIRED`, for tests)."""
    if not settings.privacy_notice_required:
        return True
    notice = await current(session, membership.org_id)
    return (
        await accepted_at(session, membership.org_id, membership.user_id, notice.version)
    ) is not None


async def accept(
    session: AsyncSession, membership: Membership, version: int, ip: str | None
) -> datetime:
    """Record that this person accepted `version`, which must be the one in
    force: a page showing an older text gets `notice_changed`, and shows the
    new one."""
    notice = await current(session, membership.org_id)
    if version != notice.version:
        raise Conflict(
            "The notice has changed since you opened it. Read the new one",
            code="notice_changed",
        )
    already = await accepted_at(session, membership.org_id, membership.user_id, version)
    if already is not None:
        return already
    row = NoticeAcceptance(org_id=membership.org_id, user_id=membership.user_id, version=version)
    session.add(row)
    await audit.record(
        session,
        action="privacy_notice.accept",
        org_id=membership.org_id,
        actor_user_id=membership.user_id,
        target_type="privacy_notice",
        target_id=str(version),
        meta={"version": version},
        ip=ip,
    )
    await session.commit()
    await session.refresh(row)
    return row.accepted_at


async def update(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    actor_id: uuid.UUID,
    body: str,
    ask_again: bool,
    ip: str | None,
) -> Notice:
    """Save the owner's text as a new revision; a new version when they ask
    everyone to accept it again."""
    text = body.strip()
    before = await _latest(session, org_id)
    version = (before.version if before else 1) + (1 if ask_again else 0)
    revision = (before.revision if before else 0) + 1
    session.add(
        PrivacyNotice(
            org_id=org_id, revision=revision, version=version, body=text, created_by=actor_id
        )
    )
    await audit.record(
        session,
        action="privacy_notice.update",
        org_id=org_id,
        actor_user_id=actor_id,
        target_type="privacy_notice",
        target_id=str(version),
        meta={"version": version, "revision": revision, "ask_again": ask_again},
        ip=ip,
    )
    await session.commit()
    return await current(session, org_id)


async def revisions(session: AsyncSession, org_id: uuid.UUID) -> list[PrivacyNotice]:
    """Every saved text, newest first: what each version said."""
    return list(
        (
            await session.scalars(
                select(PrivacyNotice)
                .where(PrivacyNotice.org_id == org_id)
                .order_by(PrivacyNotice.revision.desc())
            )
        ).all()
    )


async def acceptances(session: AsyncSession, org_id: uuid.UUID, version: int) -> int:
    """How many people accepted `version`."""
    return int(
        await session.scalar(
            select(func.count())
            .select_from(NoticeAcceptance)
            .where(NoticeAcceptance.org_id == org_id, NoticeAcceptance.version == version)
        )
        or 0
    )


# ── admins' reads, shown to the person read ──────────────────


@dataclass(frozen=True)
class Read:
    conversation_id: uuid.UUID | None
    conversation_title: str
    read_at: datetime
    reader: str
    reader_role: str
    reason: str


async def reads_of(
    session: AsyncSession, membership: Membership, *, limit: int, cursor: str | None
) -> PageResult[Read]:
    """Each time an admin opened one of this person's conversations: when,
    who and why, newest first."""
    page = await keyset_page(
        session,
        select(AuditLog).where(
            AuditLog.org_id == membership.org_id,
            AuditLog.action == conversation_reads.ACTION,
            AuditLog.meta["owner_id"].as_string() == str(membership.user_id),
        ),
        [AuditLog.created_at, AuditLog.id],
        limit=limit,
        cursor=cursor,
    )
    ids = {_uuid(r.target_id) for r in page.items} - {None}
    titles = {
        c.id: c.title
        for c in (await session.scalars(select(Conversation).where(Conversation.id.in_(ids))))
    }
    items = []
    for r in page.items:
        cid = _uuid(r.target_id)
        meta: dict[str, Any] = r.meta or {}
        items.append(
            Read(
                conversation_id=cid,
                conversation_title=titles.get(cid, "A conversation") if cid else "A conversation",
                read_at=r.created_at,
                reader=str(meta.get("reader_email") or r.actor_label or "An admin"),
                reader_role=str(meta.get("reader_role") or ""),
                reason=str(meta.get("reason") or ""),
            )
        )
    return PageResult(items=items, next_cursor=page.next_cursor)


def _uuid(value: str | None) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


# ── an assistant's card ──────────────────────────────────────


@dataclass(frozen=True)
class Use:
    """One thing an assistant uses, in words, and whether an admin approves
    each time it acts."""

    label: str
    approval: bool = False


@dataclass(frozen=True)
class About:
    version_number: int | None
    approved_at: datetime | None
    approved_by: str | None
    owner: str | None
    knowledge: list[str]
    lookups: list[Use]
    actions: list[Use]
    memory_tool: bool
    keeps_history: bool
    leaves: list[Processor]


def _hosts(domains: list[str]) -> str:
    shown = ", ".join(domains[:5])
    return shown + (f" and {len(domains) - 5} more" if len(domains) > 5 else "")  # noqa: PLR2004


async def _http_actions(
    session: AsyncSession,
    config: AssistantConfig,
    manifest: dict[str, Any],
    lookups: list[Use],
    actions: list[Use],
    leaves: list[Processor],
) -> None:
    """Its calls to company systems (Phase 9.3), as with database actions,
    and each system they reach, with whether it is told who you are."""
    http = config.tools.http_request
    if not http.enabled:
        return
    for call in http.actions:
        if call.tier == "read":
            lookups.append(Use(call.title))
        else:
            actions.append(Use(call.title, call.tier == "approver"))
    called = {a.connection_id for a in http.actions}
    for entry in manifest.get("http_connections", []):
        hconn = await session.get(HttpConnection, _uuid(entry.get("id")))
        if hconn is None or str(hconn.id) not in called:
            continue
        host = urlparse(hconn.base_url).hostname or hconn.name
        what = "The details of each request" + (
            ", and your name and email" if hconn.send_asker else ""
        )
        leaves.append(Processor(f"{hconn.name} ({host})", what + "."))


async def about(session: AsyncSession, assistant: Assistant) -> About:
    """What an assistant's approved version uses and sends out, from that
    version alone: what an admin approved, not the draft."""
    version = (
        await session.get(AssistantVersion, assistant.current_version_id)
        if assistant.current_version_id
        else None
    )
    owner = await session.get(User, assistant.created_by) if assistant.created_by else None
    owner_name = (owner.name or owner.email) if owner else None
    if version is None:
        return About(None, None, None, owner_name, [], [], [], False, False, [])
    config = AssistantConfig.model_validate(version.config)
    manifest: dict[str, Any] = version.manifest or {}
    reviewer = await session.get(User, version.reviewed_by) if version.reviewed_by else None

    knowledge = [s["name"] for s in (manifest.get("knowledge") or {}).get("sources", [])]
    lookups: list[Use] = []
    actions: list[Use] = []
    leaves = [processors()[0]]
    if config.rag.enabled and knowledge and settings.voyage_api_key and not settings.rag_offline:
        leaves.append(Processor("Voyage AI", "Your questions, to search its knowledge."))

    names = {d["id"]: d["name"] for d in manifest.get("databases", [])}
    for ref in config.databases:
        name = names.get(ref.connection_id)
        if name is None:
            continue
        lookups.append(Use(f"Looks up data in {name}"))
        if ref.expose_write and config.approval_policy.db_write != "deny":
            actions.append(
                Use(f"Changes data in {name}", config.approval_policy.db_write == "require")
            )
        # Its named actions (Phase 9.1), each as people would say it. One you
        # confirm yourself isn't an approver's, so it is not marked as one.
        for action in ref.actions:
            if action.tier == "read":
                lookups.append(Use(action.title))
            else:
                actions.append(Use(action.title, action.tier == "approver"))

    web = config.tools.web_search
    if web.enabled:
        where = f" (only {_hosts(web.allowed_domains)})" if web.allowed_domains else ""
        lookups.append(Use(f"Searches the web{where}"))
        leaves.append(
            Processor("Web search, through Anthropic", "Searches made from your questions.")
        )
    http = config.tools.http_request
    if http.enabled and http.approval != "deny" and (http.allowed_domains or not http.actions):
        hosts = _hosts(http.allowed_domains)
        lookups.append(Use(f"Reads pages on {hosts}", http.approval == "require"))
        leaves.append(Processor(hosts, "Requests the assistant makes to read a page."))
    await _http_actions(session, config, manifest, lookups, actions, leaves)

    for entry in manifest.get("mcp_servers", []):
        server = await session.get(McpServer, _uuid(entry.get("id")))
        name = str(entry.get("name") or "a connected service")
        mref = next((m for m in config.mcp_servers if m.id == entry.get("id")), None)
        mode = (mref.approval if mref else None) or config.approval_policy.mcp_default
        tools = len(entry.get("tools") or {})
        label = f"Uses {name}" + (f" ({tools} tools)" if tools else "")
        (actions if mode == "require" else lookups).append(Use(label, mode == "require"))
        if server is not None and server.url:
            host = urlparse(server.url).hostname or name
            leaves.append(Processor(f"{name} ({host})", "What the assistant sends it."))

    return About(
        version_number=version.version_number,
        approved_at=version.reviewed_at,
        approved_by=(reviewer.name or reviewer.email) if reviewer else None,
        owner=owner_name,
        knowledge=knowledge,
        lookups=lookups,
        actions=actions,
        memory_tool=config.memory.memory_tool,
        keeps_history=config.memory.persist_history,
        leaves=leaves,
    )


__all__ = [
    "BODY_MAX",
    "DEFAULT_BODY",
    "About",
    "Notice",
    "Processor",
    "Read",
    "Use",
    "about",
    "accept",
    "acceptances",
    "accepted_at",
    "contact",
    "current",
    "has_accepted",
    "processors",
    "reads_of",
    "revisions",
    "update",
]
