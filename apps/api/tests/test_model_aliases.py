"""Model aliases, the 5.5 models, and models that retire (Phase 7a.6).

Approved versions used to store dated model ids, checked strictly on every
turn: removing a retired model from the list would have broken every
assistant on it at once, and moving to a new model meant a new version and a
new approval for each. Now an assistant names a family ("sonnet") and the
operator repoints it; a pinned id that retires keeps validating and runs as
its replacement. Nothing sends an alias to the CLI or the API.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from app import preflight
from app.agent import models
from app.agent.models import (
    ALLOWED_MODELS,
    MODEL_ALIASES,
    PINNED_MODELS,
    aliases,
    always_thinks,
    fallback_for,
    price_per_mtok,
    resolve_model,
)
from app.agent.options import build_claude_options, build_runtime_spec
from app.agent.subagents import build_agent_definitions
from app.config import settings
from app.schemas.assistant_config import AssistantConfig
from httpx import AsyncClient
from pydantic import ValidationError

pytestmark = pytest.mark.anyio

API = "/api/v1"


async def _allow(*_a: Any, **_k: Any) -> Any:
    return None


def _config(**over: Any) -> AssistantConfig:
    return AssistantConfig.model_validate(over)


# ── names ────────────────────────────────────────────────────


def test_the_5_5_models_are_offered_at_their_published_prices() -> None:
    for model in ("claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-1"):
        assert model in PINNED_MODELS
    assert price_per_mtok("claude-sonnet-5-5") == (2.0, 10.0)
    assert price_per_mtok("claude-opus-5-5") == (4.0, 20.0)
    assert price_per_mtok("claude-fable-5-1") == (10.0, 50.0)
    assert price_per_mtok("sonnet") == price_per_mtok("claude-sonnet-5-5"), "an alias is priced"


def test_each_alias_means_one_pinned_model() -> None:
    assert resolve_model("haiku") == "claude-haiku-4-5"
    assert resolve_model("sonnet") == "claude-sonnet-5-5"
    assert resolve_model("opus") == "claude-opus-5-5"
    assert resolve_model("fable") == "claude-fable-5-1"
    assert set(MODEL_ALIASES.values()) <= PINNED_MODELS
    assert resolve_model("claude-sonnet-5") == "claude-sonnet-5", "a pinned id is itself"


def test_the_operator_repoints_an_alias_and_every_assistant_on_it_follows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "model_aliases", {"haiku": "claude-sonnet-5-5"})
    assert aliases()["haiku"] == "claude-sonnet-5-5"
    spec = build_runtime_spec(_config(models={"main": {"model": "haiku"}}))
    assert spec.model == "claude-sonnet-5-5"


def test_a_retired_id_still_validates_and_runs_as_its_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A version approved on a model that later retires must not stop
    loading: it runs as the replacement until someone moves it."""
    monkeypatch.setattr(models, "RETIRED_MODELS", {"claude-sonnet-5": "claude-sonnet-5-5"})
    config = _config(models={"main": {"model": "claude-sonnet-5"}})
    assert config.models.main.model == "claude-sonnet-5", "stored as written"
    assert build_runtime_spec(config).model == "claude-sonnet-5-5"


def test_an_unknown_model_is_still_refused() -> None:
    with pytest.raises(ValidationError):
        _config(models={"main": {"model": "claude-sonnet-9"}})
    with pytest.raises(ValidationError):
        _config(models={"main": {"model": "gpt-5"}})


def test_new_assistants_follow_the_families() -> None:
    config = AssistantConfig()
    assert config.models.main.model == "sonnet"
    assert config.models.judge.model == "opus"
    assert config.models.router.model == config.models.subagent.model == "haiku"
    assert {"sonnet", "opus", "haiku", "fable"} <= ALLOWED_MODELS


# ── nothing sends an alias ───────────────────────────────────


def test_the_cli_and_its_subagents_get_pinned_ids_only() -> None:
    """The SDK takes "sonnet" too, but then the CLI picks the model."""
    config = _config(
        models={"main": {"model": "sonnet"}, "subagent": {"model": "opus"}},
        subagents={"retrieval": True},
        rag={"enabled": True},
    )
    spec = build_runtime_spec(config, assistant_id=uuid.uuid4())
    options = build_claude_options(spec, _allow)
    assert options.model == "claude-sonnet-5-5"
    assert options.fallback_model in PINNED_MODELS
    for agent in build_agent_definitions(spec.subagents).values():
        assert agent.model in PINNED_MODELS, agent.model


def test_a_fallback_is_found_for_an_alias() -> None:
    assert fallback_for("sonnet") == "claude-opus-5-5"
    assert fallback_for("opus") == "claude-sonnet-5-5"
    assert fallback_for("fable") == "claude-opus-5-5"
    for model in PINNED_MODELS:
        assert fallback_for(model) in PINNED_MODELS
        assert fallback_for(model) != model


# ── thinking that can't be turned off ────────────────────────


def test_opus_5_5_and_fable_5_1_always_think() -> None:
    """`thinking: disabled` is a 400 on them, so a turn asks for adaptive
    whatever the assistant's setting says."""
    for model in ("opus", "claude-opus-5-5", "fable"):
        assert always_thinks(model)
        config = _config(models={"main": {"model": model, "thinking": {"type": "disabled"}}})
        assert build_runtime_spec(config).thinking_adaptive is True, model
    assert not always_thinks("sonnet")


def test_a_model_that_can_stop_thinking_still_can() -> None:
    config = _config(models={"main": {"model": "sonnet", "thinking": {"type": "disabled"}}})
    assert build_runtime_spec(config).thinking_adaptive is False


def test_a_subagent_on_an_always_thinking_model_turns_thinking_on() -> None:
    """A subagent runs under the turn's thinking setting."""
    config = _config(
        models={
            "main": {"model": "sonnet", "thinking": {"type": "disabled"}},
            "subagent": {"model": "opus"},
        },
        subagents={"retrieval": True},
        rag={"enabled": True},
    )
    spec = build_runtime_spec(config, assistant_id=uuid.uuid4())
    assert spec.subagents, "the retrieval subagent is in this turn"
    assert spec.thinking_adaptive is True


# ── what the builder is offered ──────────────────────────────


async def test_the_builder_is_offered_aliases_first_and_told_what_they_mean(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "model_aliases", {"haiku": "claude-sonnet-5-5"})
    body = (await client.get(f"{API}/meta/config-schema")).json()
    assert body["allowed_models"][:4] == ["haiku", "sonnet", "opus", "fable"]
    assert set(body["allowed_models"][4:]) == PINNED_MODELS
    assert body["model_aliases"]["haiku"] == "claude-sonnet-5-5"
    assert body["default_model_by_role"]["main"] == "sonnet"


# ── the preflight ────────────────────────────────────────────


def test_the_preflight_refuses_an_alias_or_model_it_does_not_know() -> None:
    for bad in ({"sonet": "claude-sonnet-5-5"}, {"haiku": "claude-haiku-5"}):
        report = preflight.Report()
        preflight.review_models(SimpleSettings(bad), report)
        assert [f.level for f in report.findings] == ["fail"], bad
        assert report.findings[0].name == "MODEL_ALIASES"
    good = preflight.Report()
    preflight.review_models(SimpleSettings({"haiku": "claude-sonnet-5-5"}), good)
    assert good.findings == []


class SimpleSettings:
    def __init__(self, model_aliases: dict[str, str]) -> None:
        self.model_aliases = model_aliases


async def test_the_preflight_names_versions_on_a_model_that_is_going(
    client: AsyncClient, org_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    aid = (await client.post(f"{API}/assistants", json={"name": "A"}, headers=org_headers)).json()[
        "id"
    ]
    cfg = AssistantConfig().model_dump(mode="json")
    cfg["models"]["main"]["model"] = "claude-sonnet-5"
    put = await client.put(f"{API}/assistants/{aid}/draft-config", json=cfg, headers=org_headers)
    assert put.status_code == 200, put.text
    await client.post(f"{API}/assistants/{aid}/versions", json={"note": "v1"}, headers=org_headers)

    assert await preflight._models_in_use() == "", "nothing deprecated: nothing to say"
    monkeypatch.setattr(models, "DEPRECATED_MODELS", {"claude-sonnet-5": "June 30, 2027"})
    with pytest.raises(RuntimeError) as said:
        await preflight._models_in_use()
    assert "claude-sonnet-5 in 1 version(s), retires June 30, 2027" in str(said.value)
    assert "'sonnet'" in str(said.value), "it says what to move to"


async def test_a_turn_records_the_model_it_ran_as_not_the_alias(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    """Run details, the message and the usage ledger name the pinned model,
    so a cost or an audit always says which model it was."""
    aid = (await client.post(f"{API}/assistants", json={"name": "A"}, headers=org_headers)).json()[
        "id"
    ]
    await client.post(f"{API}/assistants/{aid}/versions", json={"note": "v1"}, headers=org_headers)
    cid = (
        await client.post(f"{API}/assistants/{aid}/conversations", json={}, headers=org_headers)
    ).json()["id"]
    sent = await client.post(
        f"{API}/conversations/{cid}/messages", json={"text": "hello"}, headers=org_headers
    )
    assert sent.status_code == 200
    (run,) = (await client.get(f"{API}/conversations/{cid}/runs", headers=org_headers)).json()[
        "items"
    ]
    assert run["model"] == "claude-sonnet-5-5"
    messages = (await client.get(f"{API}/conversations/{cid}", headers=org_headers)).json()[
        "messages"
    ]
    assert [m["model"] for m in messages if m["role"] == "assistant"] == ["claude-sonnet-5-5"]


# ── what a turn costs ────────────────────────────────────────


def _result(model_usage: dict[str, Any], cli_total: float) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(
        usage={"input_tokens": 252, "output_tokens": 5},
        total_cost_usd=cli_total,
        session_id="s",
        num_turns=1,
        terminal_reason=None,
        model_usage=model_usage,
    )


def _used(inp: int, out: int, **more: int) -> dict[str, int]:
    return {"inputTokens": inp, "outputTokens": out, **more}


def test_a_turn_is_priced_at_our_prices_not_the_clis_guess() -> None:
    """Measured in the real check: the bundled CLI priced 252 tokens in and
    5 out at Opus 5's $5 / $25 ($0.001385) on both Sonnet 5.5 and Opus 5.5."""
    from app.agent.driver import _UsageLedger

    sonnet = _UsageLedger().settle(_result({"claude-sonnet-5-5": _used(252, 5)}, 0.001385))
    opus = _UsageLedger().settle(_result({"claude-opus-5-5": _used(252, 5)}, 0.001385))
    assert sonnet.cost_usd == pytest.approx(252 * 2 / 1e6 + 5 * 10 / 1e6)
    assert opus.cost_usd == pytest.approx(252 * 4 / 1e6 + 5 * 20 / 1e6)


def test_fable_is_not_under_counted() -> None:
    from app.agent.driver import _UsageLedger

    fable = _UsageLedger().settle(_result({"claude-fable-5-1": _used(1000, 100)}, 0.0075))
    assert fable.cost_usd == pytest.approx(1000 * 10 / 1e6 + 100 * 50 / 1e6)


def test_cache_and_web_search_are_priced_per_model() -> None:
    from app.agent.driver import _UsageLedger

    used = _used(100, 10, cacheReadInputTokens=10_000, cacheCreationInputTokens=2_000)
    opus = _UsageLedger().settle(_result({"claude-opus-5-5": {**used, "webSearchRequests": 2}}, 0))
    want = (100 * 4 + 2_000 * 4 * 1.25 + 10_000 * 4 * 0.05 + 10 * 20) / 1e6 + 2 * 0.01
    assert opus.cost_usd == pytest.approx(want)
    haiku = _UsageLedger().settle(_result({"claude-haiku-4-5": used}, 0))
    assert haiku.cost_usd == pytest.approx((100 + 2_000 * 1.25 + 10_000 * 0.1) * 1 / 1e6 + 50 / 1e6)


def test_a_subagent_on_another_model_is_priced_at_its_own_rate() -> None:
    from app.agent.driver import _UsageLedger

    both = {"claude-sonnet-5-5": _used(1000, 100), "claude-haiku-4-5": _used(1000, 100)}
    cost = _UsageLedger().settle(_result(both, 0.5)).cost_usd
    assert cost == pytest.approx((1000 * 2 + 100 * 10 + 1000 * 1 + 100 * 5) / 1e6)


def test_a_model_without_a_price_keeps_the_clis_figure() -> None:
    from app.agent.driver import _UsageLedger

    unknown = _UsageLedger().settle(_result({"claude-something-6": _used(252, 5)}, 0.0042))
    assert unknown.cost_usd == pytest.approx(0.0042)
    older_cli = _UsageLedger().settle(_result({}, 0.0042))
    assert older_cli.cost_usd == pytest.approx(0.0042), "no per-model usage reported"
