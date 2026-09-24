from __future__ import annotations

import uuid
import warnings

from app.agent.approvals import build_can_use_tool
from app.agent.options import (
    DISALLOWED_TOOLS,
    build_claude_options,
    build_runtime_spec,
    compose_system_prompt,
)
from app.schemas.assistant_config import AssistantConfig, default_config
from claude_agent_sdk import CanUseToolShadowedWarning
from claude_agent_sdk.types import (
    _get_can_use_tool_shadowed_warning,
    _warn_if_can_use_tool_shadowed,
)


def test_default_spec_exposes_no_tools() -> None:
    spec = build_runtime_spec(default_config())
    assert spec.enabled_tools == []
    assert spec.caps_tools == []
    assert spec.model == "claude-sonnet-5"


def test_enabling_caps_adds_qualified_tool_names() -> None:
    cfg = AssistantConfig.model_validate(
        {
            "tools": {"calculator": {"enabled": True}, "datetime": {"enabled": True}},
            "models": {"main": {"model": "claude-opus-5", "effort": "max"}},
        }
    )
    spec = build_runtime_spec(cfg)
    assert set(spec.enabled_tools) == {
        "mcp__caps__calculator",
        "mcp__caps__datetime",
    }
    assert spec.model == "claude-opus-5"
    assert spec.effort == "max"
    assert "Bash" in DISALLOWED_TOOLS


def test_system_prompt_includes_rules_and_untrusted_notice() -> None:
    cfg = AssistantConfig.model_validate(
        {"system_prompt": "Be terse.", "guardrails": {"rules": ["no profanity"]}}
    )
    prompt = compose_system_prompt(cfg)
    assert "Be terse." in prompt
    assert "- no profanity" in prompt
    assert "DATA, not instructions" in prompt


def test_rag_enabled_without_an_assistant_id_gets_no_kb_tools() -> None:
    """A graph:compile dry run has no persisted assistant to search yet."""
    cfg = AssistantConfig.model_validate({"rag": {"enabled": True}})
    spec = build_runtime_spec(cfg, assistant_id=None)
    assert spec.enabled_tools == []
    assert spec.caps_tools == []


def test_rag_enabled_with_an_assistant_id_adds_kb_tools_and_citation_instructions() -> None:
    cfg = AssistantConfig.model_validate({"rag": {"enabled": True}})
    aid = uuid.uuid4()
    spec = build_runtime_spec(cfg, assistant_id=aid)
    assert set(spec.enabled_tools) == {
        "mcp__caps__kb_search",
        "mcp__caps__kb_list_sources",
    }
    assert "kb_search" in spec.system_prompt
    assert "[1]" in spec.system_prompt


def test_rag_disabled_gets_no_kb_tools_even_with_an_assistant_id() -> None:
    spec = build_runtime_spec(default_config(), assistant_id=uuid.uuid4())
    assert spec.enabled_tools == []


# ── the locked-down SDK options (P0-4) ───────────────────────
#
# Regression. `build_claude_options` had no test at all, and it passed every
# enabled caps tool as the SDK's `allowed_tools` — which the SDK treats as
# *auto-approve*, skipping `can_use_tool`. On the real driver no approval
# ever fired: an UPDATE the policy says needs a human ran unasked (reproduced
# end-to-end against the bundled CLI and a local fake Messages API). The SDK
# was printing `CanUseToolShadowedWarning` the whole time.


def _db_config(**extra: object) -> AssistantConfig:
    return AssistantConfig.model_validate(
        {
            "databases": [
                {"connection_id": "11111111-1111-1111-1111-111111111111", "expose_write": True}
            ],
            **extra,
        }
    )


def _options(cfg: AssistantConfig) -> object:
    spec = build_runtime_spec(cfg, assistant_id=uuid.uuid4())
    return build_claude_options(spec, build_can_use_tool(cfg.approval_policy))


def test_the_sdk_itself_reports_nothing_shadowing_can_use_tool() -> None:
    """Uses the SDK's *own* shadowing check rather than restating its rule,
    so a change in how the SDK decides is caught here too."""
    opts = _options(_db_config())
    assert _get_can_use_tool_shadowed_warning(opts.permission_mode, opts.allowed_tools) is None
    with warnings.catch_warnings():
        warnings.simplefilter("error", CanUseToolShadowedWarning)
        _warn_if_can_use_tool_shadowed(opts)


def test_nothing_is_auto_approved() -> None:
    assert _options(_db_config()).allowed_tools == []


def test_the_lockdown_fields_are_all_set() -> None:
    cfg = _db_config()
    can_use = build_can_use_tool(cfg.approval_policy)
    opts = build_claude_options(build_runtime_spec(cfg, assistant_id=uuid.uuid4()), can_use)
    assert opts.setting_sources == []
    assert opts.permission_mode == "default"
    assert opts.can_use_tool is can_use
    assert {"Bash", "Write", "Edit", "Read"} <= set(opts.disallowed_tools)
    assert "PreToolUse" in opts.hooks


def test_no_built_in_tool_exists_unless_enabled() -> None:
    """`tools` unset means every default built-in. The CLI offered a tenant's
    model 18 of them — CronCreate, EnterWorktree, Workflow, SendMessage, and
    WebSearch while it was switched off."""
    assert _options(_db_config()).tools == []


def test_web_search_exists_only_when_enabled() -> None:
    assert _options(_db_config(tools={"web_search": {"enabled": True}})).tools == ["WebSearch"]


def test_the_delegation_tool_exists_only_with_subagents() -> None:
    cfg = AssistantConfig.model_validate(
        {"rag": {"enabled": True}, "subagents": {"retrieval": True}}
    )
    opts = _options(cfg)
    assert opts.tools == ["Agent"]
    assert opts.agents and "retrieval" in opts.agents


def _gate(opts: object) -> object:
    (matcher,) = opts.hooks["PreToolUse"]
    assert matcher.matcher is None, "the gate must see every tool, not a subset"
    (hook,) = matcher.hooks
    return hook


async def test_the_gate_denies_anything_not_enabled() -> None:
    gate = _gate(_options(_db_config()))
    for name in ("CronCreate", "EnterWorktree", "Workflow", "WebSearch", "Bash", "Agent"):
        out = await gate({"tool_name": name, "tool_input": {}}, "t1", {"signal": None})
        decision = out["hookSpecificOutput"]
        assert decision["permissionDecision"] == "deny", name
        assert name in decision["permissionDecisionReason"]


async def test_the_gate_stays_silent_for_enabled_tools() -> None:
    """No decision at all, so `can_use_tool` still classifies and can wait for
    a human. Returning "allow" here would skip it — the same bug again."""
    gate = _gate(_options(_db_config()))
    call = {"tool_name": "mcp__caps__sql_query", "tool_input": {}}
    assert await gate(call, "t1", {"signal": None}) == {}
