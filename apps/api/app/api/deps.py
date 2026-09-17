"""FastAPI dependencies: auth, current user, org scoping + RBAC."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, Path, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import BadRequest, Forbidden, NotFound, Unauthorized
from app.db.session import get_session
from app.models.assistant import Assistant
from app.models.conversation import Conversation
from app.models.enums import MemberRole
from app.models.membership import Membership
from app.models.user import User
from app.security.tokens import TokenError, decode_access_token

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


ClientIP = Annotated[str | None, Depends(client_ip)]


async def get_current_user(
    session: SessionDep,
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
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_org_membership(
    session: SessionDep,
    user: CurrentUser,
    org_id: Annotated[uuid.UUID, Path()],
) -> Membership:
    membership = await session.scalar(
        select(Membership).where(Membership.org_id == org_id, Membership.user_id == user.id)
    )
    if membership is None:
        # Don't disclose whether the org exists.
        raise NotFound("Organization not found")
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
    membership = await session.scalar(
        select(Membership).where(Membership.org_id == x_org_id, Membership.user_id == user.id)
    )
    if membership is None:
        raise NotFound("Organization not found")
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
    membership = await session.scalar(
        select(Membership).where(
            Membership.org_id == assistant.org_id, Membership.user_id == user.id
        )
    )
    if membership is None:
        raise NotFound("Assistant not found")  # don't leak cross-tenant existence
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
    membership = await session.scalar(
        select(Membership).where(Membership.org_id == conv.org_id, Membership.user_id == user.id)
    )
    if membership is None:
        raise NotFound("Conversation not found")
    assistant = await session.get(Assistant, conv.assistant_id)
    if assistant is None:
        raise NotFound("Conversation not found")
    return ConversationContext(conversation=conv, assistant=assistant, membership=membership)


ConversationCtx = Annotated[ConversationContext, Depends(get_conversation_context)]


__all__ = [
    "ActiveMembership",
    "AssistantContext",
    "AssistantCtx",
    "ClientIP",
    "ConversationContext",
    "ConversationCtx",
    "CurrentUser",
    "EditableAssistantCtx",
    "OrgMembership",
    "SessionDep",
    "active_membership",
    "client_ip",
    "get_assistant_context",
    "get_conversation_context",
    "get_current_user",
    "get_org_membership",
    "require_assistant_editor",
    "require_role",
]
