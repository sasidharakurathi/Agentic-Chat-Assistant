"""FastAPI dependencies: auth, current user, org scoping + RBAC.

Each context below, once it has verified the caller belongs to the org it is
about to act in, binds that org to the request's session
(`app.db.tenancy.bind_org`); from then on every ORM query the request makes
is limited to that org's rows, whether or not it filters by org itself.

Each also applies the Studio floor (`app.security.access`, Phase 7a.3):
the membership must hold a role that may use the Studio at all."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, Header, Path, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import ratelimit
from app.api.errors import BadRequest, Forbidden, NotFound, Unauthorized
from app.config import settings
from app.db.session import get_session
from app.db.tenancy import bind_org
from app.models.assistant import Assistant
from app.models.conversation import Conversation
from app.models.enums import MemberRole
from app.models.membership import Membership
from app.models.user import User
from app.security.access import member_of
from app.security.tokens import TokenError, decode_access_token

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@dataclass(frozen=True)
class PageParams:
    limit: int
    cursor: str | None


def page_params(
    limit: Annotated[int, Query(ge=1, le=200, description="Items per page.")] = 50,
    cursor: Annotated[
        str | None, Query(description="`next_cursor` from the previous page.")
    ] = None,
) -> PageParams:
    return PageParams(limit=limit, cursor=cursor)


PageQuery = Annotated[PageParams, Depends(page_params)]


def client_ip(request: Request) -> str | None:
    # Believes X-Forwarded-For only behind trusted proxies (task 5.8); it
    # used to take the header's first entry, which the caller chooses.
    return ratelimit.client_ip(request)


ClientIP = Annotated[str | None, Depends(client_ip)]


async def get_current_user(
    session: SessionDep,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise Unauthorized("Missing bearer token", code="missing_token")
    token = authorization.split(" ", 1)[1].strip()
    try:
        claims = decode_access_token(token)
    except TokenError as exc:
        raise Unauthorized("Invalid or expired token", code="invalid_token") from exc

    user = await session.get(User, claims.user_id)
    if user is None or not user.is_active:
        raise Unauthorized("User not found or inactive", code="invalid_token")
    # For the access log line (Phase 7a.8): who made the request.
    request.state.user_id = str(user.id)
    await ratelimit.enforce("user", str(user.id), settings.rate_limit_user)
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def per_user_limit(bucket: str, setting: str) -> Any:
    """A route dependency: a rate limit for this caller (task 5.8), from
    `settings.<setting>`. For routes that cost more than an ordinary read."""

    async def _limit(user: CurrentUser) -> None:
        await ratelimit.enforce(bucket, str(user.id), getattr(settings, setting))

    return Depends(_limit)


def per_ip_limit(bucket: str, setting: str) -> Any:
    """The same, per client IP: for routes used before anyone is signed in."""

    async def _limit(request: Request) -> None:
        await ratelimit.enforce(bucket, ratelimit.client_ip(request), getattr(settings, setting))

    return Depends(_limit)


async def get_org_membership(
    session: SessionDep,
    user: CurrentUser,
    org_id: Annotated[uuid.UUID, Path()],
) -> Membership:
    # Not found for outsiders: don't disclose whether the org exists.
    membership = await member_of(session, org_id, user.id, missing="Organization not found")
    bind_org(session, org_id)
    return membership


OrgMembership = Annotated[Membership, Depends(get_org_membership)]


def require_role(
    minimum: MemberRole,
) -> Callable[[Membership], Awaitable[Membership]]:
    async def _dep(membership: OrgMembership) -> Membership:
        if not membership.role.satisfies(minimum):
            raise Forbidden(f"Requires {minimum.value} role or higher", code="insufficient_role")
        return membership

    return _dep


# ── Active org (from the X-Org-Id header, for non-org-path routes) ────


async def active_membership(
    session: SessionDep,
    user: CurrentUser,
    x_org_id: Annotated[uuid.UUID | None, Header()] = None,
) -> Membership:
    if x_org_id is None:
        raise BadRequest("Missing X-Org-Id header", code="org_required")
    membership = await member_of(session, x_org_id, user.id, missing="Organization not found")
    bind_org(session, x_org_id)
    return membership


ActiveMembership = Annotated[Membership, Depends(active_membership)]


# ── Assistant context (loads the assistant + the caller's membership) ─


@dataclass
class AssistantContext:
    assistant: Assistant
    membership: Membership

    @property
    def can_edit(self) -> bool:
        return self.membership.role.satisfies(MemberRole.admin) or (
            self.assistant.created_by is not None
            and self.assistant.created_by == self.membership.user_id
        )


async def get_assistant_context(
    session: SessionDep,
    user: CurrentUser,
    assistant_id: Annotated[uuid.UUID, Path()],
) -> AssistantContext:
    assistant = await session.get(Assistant, assistant_id)
    if assistant is None:
        raise NotFound("Assistant not found")
    # Not found for outsiders: don't leak cross-tenant existence.
    membership = await member_of(session, assistant.org_id, user.id, missing="Assistant not found")
    bind_org(session, assistant.org_id)
    return AssistantContext(assistant=assistant, membership=membership)


AssistantCtx = Annotated[AssistantContext, Depends(get_assistant_context)]


async def require_assistant_editor(ctx: AssistantCtx) -> AssistantContext:
    if not ctx.can_edit:
        raise Forbidden(
            "You can only edit assistants you created (or need an admin role)",
            code="not_assistant_editor",
        )
    return ctx


EditableAssistantCtx = Annotated[AssistantContext, Depends(require_assistant_editor)]


# ── Conversation context ─────────────────────────────────────


@dataclass
class ConversationContext:
    conversation: Conversation
    assistant: Assistant
    membership: Membership


async def get_conversation_context(
    session: SessionDep,
    user: CurrentUser,
    conversation_id: Annotated[uuid.UUID, Path()],
) -> ConversationContext:
    conv = await session.get(Conversation, conversation_id)
    if conv is None:
        raise NotFound("Conversation not found")
    membership = await member_of(session, conv.org_id, user.id, missing="Conversation not found")
    assistant = await session.get(Assistant, conv.assistant_id)
    if assistant is None:
        raise NotFound("Conversation not found")
    bind_org(session, conv.org_id)
    return ConversationContext(conversation=conv, assistant=assistant, membership=membership)


ConversationCtx = Annotated[ConversationContext, Depends(get_conversation_context)]


async def require_conversation_starter(
    ctx: ConversationCtx, user: CurrentUser
) -> ConversationContext:
    """Posting into a conversation, stopping its answer, renaming or
    archiving it: only the person who started it.

    Admins too used to pass here (task 6.3). But a message runs the agent as
    the conversation's owner: their memory, and soon their identity for
    "my tickets" lookups and requests (plan D8). An admin posting into an
    employee's thread would act in that employee's name, and could then
    approve the write their own message caused. Admins may read any
    conversation (plan D5); reading is not a licence to act (Phase 7a.4).
    A conversation with no recorded starter can be read, not changed.
    """
    if ctx.conversation.created_by is not None and ctx.conversation.created_by == user.id:
        return ctx
    raise Forbidden(
        "Only the person who started this conversation can reply in it or change it",
        code="not_conversation_owner",
    )


ConversationStarterCtx = Annotated[ConversationContext, Depends(require_conversation_starter)]


__all__ = [
    "ActiveMembership",
    "AssistantContext",
    "AssistantCtx",
    "ClientIP",
    "ConversationContext",
    "ConversationCtx",
    "ConversationStarterCtx",
    "CurrentUser",
    "EditableAssistantCtx",
    "OrgMembership",
    "PageParams",
    "PageQuery",
    "SessionDep",
    "active_membership",
    "client_ip",
    "get_assistant_context",
    "get_conversation_context",
    "get_current_user",
    "get_org_membership",
    "per_ip_limit",
    "per_user_limit",
    "require_assistant_editor",
    "require_conversation_starter",
    "require_role",
]
