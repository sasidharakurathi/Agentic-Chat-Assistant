from __future__ import annotations

from app.agent.options import DISALLOWED_TOOLS, build_runtime_spec, compose_system_prompt
from app.schemas.assistant_config import AssistantConfig, default_config


def test_default_spec_exposes_no_tools() -> None:
    spec = build_runtime_spec(default_config())
    assert spec.allowed_tools == []
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
    assert set(spec.allowed_tools) == {
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
