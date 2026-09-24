"""``FakeDriver``'s kb_search branch — proves the driver-level plumbing
(tool-call event -> handler -> tool-result event -> reply) without needing
real Postgres/embeddings. The handler itself (real retrieve() against a real
knowledge base) is tested separately in test_agent_caps_rag.py
(``-m integration``); this only needs a synthetic CapabilityTool."""

from __future__ import annotations

from typing import Any

from app.agent.caps import CapabilityTool
from app.agent.driver import FakeDriver
from app.agent.options import RuntimeSpec
from app.schemas.assistant_config import ApprovalPolicy


async def _fake_kb_handler(args: dict[str, Any]) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": f"[1] Fake Source\nanswer for: {args['query']}"}]}


def _spec_with_kb_search() -> RuntimeSpec:
    kb_tool = CapabilityTool(
        name="kb_search",
        description="test",
        input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
        handler=_fake_kb_handler,
    )
    return RuntimeSpec(
        system_prompt="You have kb_search.",
        model="claude-sonnet-5",
        effort="high",
        thinking_adaptive=False,
        max_turns=None,
        max_budget_usd=None,
        enabled_tools=["mcp__caps__kb_search"],
        caps_tools=[kb_tool],
    )


async def test_search_prompt_triggers_the_kb_search_tool() -> None:
    driver = FakeDriver()
    events = [
        e
        async for e in driver.stream(
            prompt="please search for refund info",
            spec=_spec_with_kb_search(),
            policy=ApprovalPolicy(),
        )
    ]
    kinds = [e.type for e in events]
    assert kinds[0] == "tool_call"
    assert kinds[1] == "tool_result"
    assert kinds[-1] == "usage"

    tool_call = events[0]
    assert tool_call.name == "mcp__caps__kb_search"  # type: ignore[attr-defined]

    tool_result = events[1]
    assert "[1] Fake Source" in tool_result.output  # type: ignore[attr-defined]

    token_texts = "".join(e.text for e in events if e.type == "token")  # type: ignore[attr-defined]
    assert "Fake Source" in token_texts


async def test_prompt_without_search_does_not_trigger_kb_search() -> None:
    driver = FakeDriver()
    events = [
        e
        async for e in driver.stream(
            prompt="hello there", spec=_spec_with_kb_search(), policy=ApprovalPolicy()
        )
    ]
    assert all(e.type != "tool_call" for e in events)
