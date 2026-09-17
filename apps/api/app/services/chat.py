"""Conversation lifecycle + the turn orchestration behind the SSE endpoint."""

from __future__ import annotations

import tempfile
import time
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import session_store
from app.agent.events import AgentEvent, DoneEvent, ErrorEvent
from app.agent.runtime import Turn
from app.api.errors import NotFound
from app.models.assistant import Assistant, AssistantVersion
from app.models.conversation import (
    Conversation,
    ConversationStatus,
    Message,
    MessageRole,
    Run,
)
from app.models.usage import UsageEvent, UsageKind
from app.schemas.assistant_config import AssistantConfig

_SCRATCH_BASE = Path(tempfile.gettempdir()) / "assistant-studio" / "scratch"


def _scratch_dir(conversation_id: uuid.UUID) -> Path:
    d = _SCRATCH_BASE / str(conversation_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


async def create_conversation(
    session: AsyncSession, *, assistant: Assistant, user_id: uuid.UUID, title: str | None
) -> Conversation:
    conv = Conversation(
        assistant_id=assistant.id,
        assistant_version_id=assistant.current_version_id,
        org_id=assistant.org_id,
        created_by=user_id,
        title=(title or "New conversation").strip() or "New conversation",
    )
    session.add(conv)
    await session.flush()
    await session.commit()
    return conv


async def list_conversations(session: AsyncSession, assistant_id: uuid.UUID) -> list[Conversation]:
    rows = await session.scalars(
        select(Conversation)
        .where(Conversation.assistant_id == assistant_id)
        .order_by(Conversation.created_at.desc())
    )
    return list(rows)


async def load(session: AsyncSession, conversation_id: uuid.UUID) -> Conversation:
    conv = await session.get(Conversation, conversation_id)
    if conv is None:
        raise NotFound("Conversation not found")
    return conv


async def list_messages(session: AsyncSession, conversation_id: uuid.UUID) -> list[Message]:
    rows = await session.scalars(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at, Message.id)
    )
    return list(rows)


async def rename(session: AsyncSession, conv: Conversation, title: str) -> Conversation:
    conv.title = title.strip() or conv.title
    await session.flush()
    await session.commit()
    return conv


async def archive(session: AsyncSession, conv: Conversation) -> None:
    conv.status = ConversationStatus.archived
    await session.flush()
    await session.commit()


async def _config_for(session: AsyncSession, conv: Conversation) -> AssistantConfig:
    if conv.assistant_version_id is not None:
        version = await session.get(AssistantVersion, conv.assistant_version_id)
        if version is not None:
            return AssistantConfig.model_validate(version.config)
    assistant = await session.get(Assistant, conv.assistant_id)
    assert assistant is not None
    return AssistantConfig.model_validate(assistant.draft_config)


async def run_message(
    session: AsyncSession, *, conversation_id: uuid.UUID, text: str
) -> AsyncIterator[AgentEvent]:
    conv = await load(session, conversation_id)
    config = await _config_for(session, conv)

    cap = config.models.main.max_budget_usd
    spent = float(conv.cost_usd)
    if cap is not None and spent >= cap:
        yield ErrorEvent(
            code="budget_exceeded", message="This conversation has reached its spend limit."
        )
        return

    user_msg = Message(
        conversation_id=conv.id,
        org_id=conv.org_id,
        role=MessageRole.user,
        content=text,
    )
    session.add(user_msg)
    await session.flush()
    await session.commit()  # keep the user's message even if the client disconnects

    # Redis is a fast-path cache in front of the durable Postgres column — it
    # can only ever be as fresh or staler, never ahead, so a miss just falls
    # back to what we already loaded on the conversation row.
    cached_session_id = await session_store.get(conv.id)
    remaining = None if cap is None else max(0.0, cap - spent)
    turn = Turn(
        config,
        prompt=text,
        session_id=cached_session_id or conv.sdk_session_id,
        budget_remaining_usd=remaining,
        scratch_dir=_scratch_dir(conv.id),
    )
    started = time.perf_counter()
    async for ev in turn.stream():
        yield ev
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    o = turn.outcome

    asst_msg = Message(
        conversation_id=conv.id,
        org_id=conv.org_id,
        role=MessageRole.assistant,
        content=o.text.strip(),
        blocks=o.tool_calls,
        model=config.models.main.model,
        tokens_in=o.tokens_in,
        tokens_out=o.tokens_out,
        latency_ms=elapsed_ms,
        parent_id=user_msg.id,
    )
    session.add(asst_msg)
    await session.flush()

    run = Run(
        conversation_id=conv.id,
        message_id=asst_msg.id,
        org_id=conv.org_id,
        model=config.models.main.model,
        effort=config.models.main.effort,
        driver=o.driver_name,
        num_turns=o.num_turns,
        tokens_in=o.tokens_in,
        tokens_out=o.tokens_out,
        cost_usd=o.cost_usd,
        duration_ms=elapsed_ms,
        status=o.status,
        error=o.error,
    )
    session.add(run)

    session.add(
        UsageEvent(
            org_id=conv.org_id,
            assistant_id=conv.assistant_id,
            conversation_id=conv.id,
            kind=UsageKind.llm,
            model=config.models.main.model,
            tokens_in=o.tokens_in,
            tokens_out=o.tokens_out,
            cost_usd=o.cost_usd,
        )
    )

    conv.cost_usd = spent + o.cost_usd
    usage = dict(conv.token_usage or {})
    usage["in"] = int(usage.get("in", 0)) + o.tokens_in
    usage["out"] = int(usage.get("out", 0)) + o.tokens_out
    conv.token_usage = usage
    conv.last_message_at = datetime.now(UTC)
    if o.sdk_session_id and o.sdk_session_id != conv.sdk_session_id:
        conv.sdk_session_id = o.sdk_session_id
        await session_store.set(conv.id, o.sdk_session_id)
    await session.flush()
    await session.commit()

    yield DoneEvent(message_id=str(asst_msg.id), run_id=str(run.id))


__all__ = [
    "archive",
    "create_conversation",
    "list_conversations",
    "list_messages",
    "load",
    "rename",
    "run_message",
]
