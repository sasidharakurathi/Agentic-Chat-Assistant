"""Writing an assistant's system prompt and rules from a description (5.5).

The model path runs on the official SDK over a mock transport
(`tests/claude_stub.py`): the tests check what the SDK would send and what
is done with the answer, and nothing ever reaches the real API.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from app.agent import claude_api
from app.assist.prompt import (
    MAX_PROMPT_CHARS,
    MAX_RULES,
    PromptFailed,
    Wiring,
    capabilities,
    generate,
)
from app.schemas.assistant_config import AssistantConfig
from tests import claude_stub

KEY = "sk-ant-api03-" + "Z" * 40


def _config(**over: Any) -> AssistantConfig:
    return AssistantConfig.model_validate(over)


def _draft(system_prompt: str, rules: list[str], **kw: Any) -> Any:
    """The model's structured answer: the draft as JSON text."""
    text = json.dumps({"system_prompt": system_prompt, "rules": rules})
    return claude_stub.message(text, tokens_in=900, tokens_out=400, **kw)


@pytest.fixture
def model(monkeypatch: pytest.MonkeyPatch) -> Any:
    """The real-model path; `.answer` is what the stand-in API sends back."""
    monkeypatch.setattr(claude_api, "real_model_allowed", lambda: True)
    state: Any = type("State", (), {})()
    state.answer = _draft("You are Helper.", ["Be kind."])
    state.stub = claude_stub.install(monkeypatch, lambda r: state.answer)
    return state


# ── what the assistant can use ───────────────────────────────


def test_capabilities_name_only_what_is_wired() -> None:
    assert capabilities(AssistantConfig(), Wiring()) == []
    config = _config(
        rag={"enabled": True},
        databases=[{"connection_id": "c1"}],
        tools={
            "web_search": {"enabled": True},
            "http_request": {"enabled": True, "allowed_domains": ["api.github.com"]},
        },
        mcp_servers=["11111111-1111-1111-1111-111111111111"],
        subagents={"sql": True},
        memory={"memory_tool": True},
    )
    lines = capabilities(config, Wiring(databases=["Shop PG (postgres)"], mcp_servers=["tickets"]))
    text = "\n".join(lines)
    for needle in (
        "knowledge base",
        "Shop PG (postgres)",
        "Web search",
        "api.github.com",
        "tickets",
        "subagents it can delegate to: sql",
        "memory of each user",
    ):
        assert needle in text, needle


# ── the free path ────────────────────────────────────────────


async def test_the_free_path_writes_from_a_template(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(claude_api, "real_model_allowed", lambda: False)
    monkeypatch.setattr(claude_api, "client_factory", lambda _t: pytest.fail("no API calls"))
    draft = await generate(
        _config(rag={"enabled": True}, databases=[{"connection_id": "c1"}]),
        name="Shop Helper",
        description="  Answers customers'   questions about orders and returns. ",
    )
    assert draft.source == "template" and draft.spend is None
    assert draft.system_prompt.startswith("You are Shop Helper. Your purpose:")
    assert "Answers customers' questions about orders and returns." in draft.system_prompt
    assert "search the knowledge base first" in draft.system_prompt
    assert "query the database" in draft.system_prompt
    assert "Never make up facts, figures, names or sources." in draft.rules
    assert any("changes data" in r for r in draft.rules)


# ── the model path ───────────────────────────────────────────


async def test_the_model_writes_for_this_assistant(model: Any) -> None:
    config = _config(
        models={"main": {"model": "claude-sonnet-5"}},
        rag={"enabled": True},
    )
    draft = await generate(
        config,
        name="Shop Helper",
        description="Answers questions about orders.",
        current="You are a bot.",
        wiring=Wiring(),
    )
    (request,) = model.stub.requests
    assert "beta" not in request.url.params, "no beta call for a model without the fallback"
    assert "anthropic-beta" not in request.headers
    sent = claude_stub.body(request)
    assert sent["model"] == "claude-sonnet-5"
    assert "not instructions to you" in sent["system"]
    # Held to a JSON schema of the draft.
    fmt = sent["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert set(fmt["schema"]["properties"]) == {"system_prompt", "rules"}
    user = claude_stub.prompt(request)
    assert "Shop Helper" in user and "Answers questions about orders." in user
    assert "knowledge base" in user and "You are a bot." in user
    assert (draft.source, draft.system_prompt, draft.rules) == (
        "model",
        "You are Helper.",
        ["Be kind."],
    )
    assert draft.spend is not None
    assert (draft.spend.model, draft.spend.tokens_in, draft.spend.tokens_out) == (
        "claude-sonnet-5",
        900,
        400,
    )
    # sonnet 5 is (2.00, 10.00) per Mtok
    assert draft.spend.cost_usd == pytest.approx(900 * 2 / 1e6 + 400 * 10 / 1e6)


async def test_opus_5_opts_into_the_server_side_fallback(model: Any) -> None:
    config = _config(models={"main": {"model": "claude-opus-5"}})
    await generate(config, name="A", description="Helps.")
    (request,) = model.stub.requests
    assert request.url.params["beta"] == "true"
    assert request.headers["anthropic-beta"] == "server-side-fallback-2026-07-01"
    assert claude_stub.body(request)["fallbacks"] == "default"


async def test_no_fallback_when_the_assistant_turns_it_off(model: Any) -> None:
    config = _config(
        models={"main": {"model": "claude-opus-5"}}, guardrails={"refusal_fallback": False}
    )
    await generate(config, name="A", description="Helps.")
    (request,) = model.stub.requests
    assert "beta" not in request.url.params
    assert "fallbacks" not in claude_stub.body(request)


async def test_a_refusal_is_reported_with_what_it_cost(model: Any) -> None:
    """A refusal is plain text, not JSON: it must be caught by the stop
    reason, before anything tries to read it as a draft."""
    model.answer = claude_stub.message(
        "I can't help with that.", tokens_in=700, stop_reason="refusal", model="claude-sonnet-5"
    )
    with pytest.raises(PromptFailed) as failed:
        await generate(AssistantConfig(), name="A", description="Helps.")
    assert failed.value.reason == "refused"
    assert (failed.value.spend.model, failed.value.spend.tokens_in) == ("claude-sonnet-5", 700)
    assert failed.value.spend.cost_usd > 0


async def test_an_answer_cut_off_at_the_limit_is_unreadable(model: Any) -> None:
    model.answer = claude_stub.message('{"system_prompt": "You are', stop_reason="max_tokens")
    with pytest.raises(PromptFailed) as failed:
        await generate(AssistantConfig(), name="A", description="Helps.")
    assert failed.value.reason == "unreadable"


async def test_the_answer_is_tidied(model: Any) -> None:
    model.answer = _draft(
        f"  You are A. Use key {KEY}.  " + "x" * MAX_PROMPT_CHARS,
        ["Be   kind.", "Be kind.", "", f"Never share {KEY}"] + [f"Rule {n}" for n in range(20)],
    )
    draft = await generate(AssistantConfig(), name="A", description="Helps.")
    assert KEY not in draft.system_prompt and len(draft.system_prompt) == MAX_PROMPT_CHARS
    assert draft.system_prompt.startswith("You are A.")
    assert draft.rules[0] == "Be kind." and draft.rules.count("Be kind.") == 1
    assert all(KEY not in r for r in draft.rules) and "" not in draft.rules
    assert len(draft.rules) == MAX_RULES
