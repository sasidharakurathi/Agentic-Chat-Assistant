"""Subagents at runtime (task 2.10).

- A subagent's own text is not the answer. With `forward_subagent_text` the
  SDK streams it, tagged with the delegation call it runs under
  (`parent_tool_use_id`); the driver used to drop that tag, so the retrieval
  subagent's working notes streamed, and were saved, as part of the answer.
- A subagent's model calls cost money but are not the main agent's turns.
- The subagent model role's `max_turns` / `effort`, and a subagent node's
  overrides, reach the SDK's agent definition.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from app.agent.driver import _events_for, _PartialText, _UsageLedger
from app.agent.events import ThinkingEvent, TokenEvent, ToolCallEvent, ToolResultEvent, UsageEvent
from app.agent.options import SUBAGENT_TOOL
from app.agent.subagents import RETRIEVAL_MAX_TURNS, build_agent_definitions, build_subagent_specs
from app.db.session import get_sessionmaker
from app.graph.compile import compile_graph
from app.graph.project import project_config
from app.models.conversation import Message, MessageRole
from app.schemas.assistant_config import AssistantConfig
from app.services import chat as chat_svc
from claude_agent_sdk import (
    AssistantMessage,
    StreamEvent,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from httpx import AsyncClient
from sqlalchemy import select
from tests.test_chat import _new_assistant, _new_conversation

pytestmark = pytest.mark.anyio

TASK = "toolu_task"


def _assistant(content: list[Any], parent: str | None, mid: str = "m1") -> AssistantMessage:
    return AssistantMessage(
        content=content,
        model="claude-haiku-4-5",
        parent_tool_use_id=parent,
        message_id=mid,
        usage={"input_tokens": 100, "output_tokens": 10},
    )


# ── the driver keeps the tag ─────────────────────────────────────────


def test_a_subagents_text_and_calls_carry_their_parent() -> None:
    msg = _assistant(
        [
            TextBlock(text="Looking in the handbook."),
            ToolUseBlock(id="toolu_kb", name="mcp__caps__kb_search", input={"query": "q"}),
        ],
        parent=TASK,
    )
    token, call, usage = _events_for(msg, set(), _UsageLedger())
    assert isinstance(token, TokenEvent) and token.parent_id == TASK
    assert isinstance(call, ToolCallEvent) and call.parent_id == TASK
    assert isinstance(usage, UsageEvent)
    assert usage.cost_usd > 0, "a subagent's call still costs money"
    assert usage.model_calls == 0, "but is not a turn of the main agent"


def test_the_main_agents_messages_have_no_parent() -> None:
    token, usage = _events_for(_assistant([TextBlock(text="Answer.")], None), set(), _UsageLedger())
    assert isinstance(token, TokenEvent) and token.parent_id is None
    assert isinstance(usage, UsageEvent) and usage.model_calls == 1


def test_streamed_subagent_deltas_and_results_carry_their_parent() -> None:
    partial = _PartialText()
    start = StreamEvent(
        uuid="u",
        session_id="s",
        event={"type": "message_start", "message": {"id": "m9"}},
        parent_tool_use_id=TASK,
    )
    delta = StreamEvent(
        uuid="u",
        session_id="s",
        event={
            "type": "content_block_delta",
            "delta": {"type": "thinking_delta", "thinking": "hm"},
        },
        parent_tool_use_id=TASK,
    )
    assert _events_for(start, set(), partial=partial) == []
    (thinking,) = _events_for(delta, set(), partial=partial)
    assert isinstance(thinking, ThinkingEvent) and thinking.parent_id == TASK

    result = UserMessage(
        content=[ToolResultBlock(tool_use_id="toolu_kb", content="passages")],
        parent_tool_use_id=TASK,
    )
    (res,) = _events_for(result, set())
    assert isinstance(res, ToolResultEvent) and res.parent_id == TASK


# ── the turn keeps it out of the answer ──────────────────────────────


async def test_a_delegated_turn_keeps_the_subagents_notes_off_the_answer(
    client: AsyncClient, org_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def no_hits(*_a: Any, **_k: Any) -> list[Any]:
        return []

    monkeypatch.setattr("app.agent.caps_rag.retrieve", no_hits)
    aid = await _new_assistant(
        client,
        org_headers,
        {"rag": {"enabled": True}, "subagents": {"retrieval": True}},
    )
    cid = uuid.UUID(await _new_conversation(client, org_headers, aid))
    async with get_sessionmaker()() as session:
        events = [
            e
            async for e in chat_svc.run_message(
                session, conversation_id=cid, text="search the handbook for refunds"
            )
        ]

    streamed = [e for e in events if e.type == "token"]
    assert any(getattr(e, "parent_id", None) == "fake-task-1" for e in streamed)

    async with get_sessionmaker()() as s:
        (answer,) = (
            await s.scalars(
                select(Message).where(
                    Message.conversation_id == cid, Message.role == MessageRole.assistant
                )
            )
        ).all()
    assert "Searching the knowledge base" not in answer.content
    assert "No relevant results" in answer.content
    calls = {b["id"]: b for b in answer.blocks if b.get("type") == "tool_call"}
    task = calls["fake-task-1"]
    assert task["name"] == SUBAGENT_TOOL
    assert "Searching the knowledge base" in task["subagent_text"]
    assert calls["fake-sub-1"]["parent_id"] == "fake-task-1"


# ── limits reach the agent definition ────────────────────────────────


def test_the_subagent_role_limits_reach_the_agent_definition() -> None:
    config = AssistantConfig.model_validate(
        {
            "rag": {"enabled": True},
            "subagents": {"retrieval": True},
            "models": {"subagent": {"model": "claude-haiku-4-5", "max_turns": 3, "effort": "low"}},
        }
    )
    (spec,) = build_subagent_specs(config)
    assert (spec.max_turns, spec.effort) == (3, "low")
    definition = build_agent_definitions([spec])["retrieval"]
    assert (definition.maxTurns, definition.effort) == (3, "low")

    default = AssistantConfig.model_validate(
        {"rag": {"enabled": True}, "subagents": {"retrieval": True}}
    )
    assert build_subagent_specs(default)[0].max_turns == RETRIEVAL_MAX_TURNS


def _graph_with_subagent(data: dict[str, Any]) -> Any:
    from app.graph.nodes import Graph

    config = AssistantConfig.model_validate(
        {"rag": {"enabled": True}, "subagents": {"retrieval": True}}
    )
    graph = project_config(config).model_dump(mode="json")
    for node in graph["nodes"]:
        if node["type"] == "subagent":
            node["data"].update(data)
    return Graph.model_validate(graph)


def test_a_subagent_nodes_overrides_are_compiled_not_dropped() -> None:
    config = compile_graph(
        _graph_with_subagent(
            {"max_turns": 4, "model": {"model": "claude-sonnet-5", "effort": "medium"}}
        )
    )
    # The node's settings are its role's own (task 5.1); the shared subagent
    # role, which the other subagents use, is left alone.
    own = config.subagents.models["retrieval"]
    assert (own.model, own.max_turns) == ("claude-sonnet-5", 4)
    assert config.models.subagent.model == "haiku"
    (spec,) = build_subagent_specs(config)
    assert (spec.model, spec.max_turns) == ("claude-sonnet-5", 4)


def test_a_subagent_node_without_overrides_keeps_the_role() -> None:
    config = AssistantConfig.model_validate(
        {
            "rag": {"enabled": True},
            "subagents": {"retrieval": True},
            "models": {"subagent": {"model": "claude-haiku-4-5", "max_turns": 7}},
        }
    )
    again = compile_graph(project_config(config))
    assert again.models.subagent == config.models.subagent
