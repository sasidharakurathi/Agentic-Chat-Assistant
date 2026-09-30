"""Refusals, fallback, retries and timeouts (task 5.4).

- The CLI is given the fallback model and the retry/timeout settings.
- The real driver turns failures into typed errors, and reads why the model
  stopped and which models answered.
- The runtime restarts a turn whose runtime failed before saying anything,
  but never one that already streamed, and never for API trouble the CLI
  has already retried.
- A refusal is recorded as one, not as an error; an answer from the fallback
  model is recorded on the run.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from app.agent import driver as driver_mod
from app.agent import runtime as runtime_mod
from app.agent.driver import ClaudeSDKDriver, FakeDriver, _events_for
from app.agent.events import ErrorEvent, TokenEvent, UsageEvent
from app.agent.models import ALLOWED_MODELS, fallback_for
from app.agent.options import build_claude_options, build_runtime_spec
from app.config import settings
from app.db.session import get_sessionmaker
from app.models.conversation import Run
from app.schemas.assistant_config import ApprovalPolicy, AssistantConfig
from claude_agent_sdk import AssistantMessage, CLIConnectionError, ResultMessage
from httpx import AsyncClient
from sqlalchemy import select

from test_chat_approvals import _run_turn  # type: ignore[import-not-found]

# ── what the CLI is given ────────────────────────────────────


async def _allow(_name: str, _input: dict[str, Any], _ctx: Any) -> Any:
    return None


def test_every_model_has_a_different_fallback() -> None:
    for model in ALLOWED_MODELS:
        fallback = fallback_for(model)
        assert fallback in ALLOWED_MODELS and fallback != model


def test_the_cli_gets_the_fallback_and_the_resilience_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "agent_max_retries", 3)
    monkeypatch.setattr(settings, "agent_stream_idle_timeout_ms", 45_000)
    options = build_claude_options(build_runtime_spec(AssistantConfig()), _allow)
    assert options.fallback_model == fallback_for(AssistantConfig().models.main.model)
    assert options.env["CLAUDE_CODE_MAX_RETRIES"] == "3"
    assert options.env["CLAUDE_STREAM_IDLE_TIMEOUT_MS"] == "45000"
    assert options.env["API_TIMEOUT_MS"] == str(settings.agent_api_timeout_ms)
    off = AssistantConfig.model_validate({"guardrails": {"refusal_fallback": False}})
    assert build_claude_options(build_runtime_spec(off), _allow).fallback_model is None


# ── the real driver ──────────────────────────────────────────


def test_a_failed_model_call_on_a_message_becomes_a_typed_error() -> None:
    msg = AssistantMessage(content=[], model="m", message_id="m1", error="rate_limit")
    (event,) = [e for e in _events_for(msg, denied=set()) if isinstance(e, ErrorEvent)]
    assert (event.code, event.retryable) == ("rate_limited", True)
    # A subagent's failed call is not the turn's.
    sub = AssistantMessage(
        content=[], model="m", message_id="m2", error="rate_limit", parent_tool_use_id="t1"
    )
    assert not [e for e in _events_for(sub, denied=set()) if isinstance(e, ErrorEvent)]


def test_the_final_report_says_why_it_stopped_and_who_answered() -> None:
    result = ResultMessage(
        subtype="success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=False,
        num_turns=1,
        session_id="s",
        stop_reason="refusal",
        usage={"input_tokens": 1, "output_tokens": 1},
        model_usage={"claude-sonnet-5": {}, "claude-opus-5": {}},  # type: ignore[dict-item]
    )
    (event,) = _events_for(result, denied=set())
    assert isinstance(event, UsageEvent)
    assert (event.stop_reason, event.models) == ("refusal", ["claude-opus-5", "claude-sonnet-5"])


@pytest.mark.anyio
async def test_the_real_driver_reports_a_typed_failure_not_the_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def factory(_options: Any) -> Any:
        raise CLIConnectionError("spawn failed: /opt/secret/path")

    monkeypatch.setattr(driver_mod, "transport_factory", factory)
    events = [
        e
        async for e in ClaudeSDKDriver().stream(
            prompt="hi", spec=build_runtime_spec(AssistantConfig()), policy=ApprovalPolicy()
        )
    ]
    (error,) = events
    assert isinstance(error, ErrorEvent)
    assert (error.code, error.retryable) == ("agent_unavailable", True)
    assert "/opt/secret" not in error.message


# ── the runtime, through a conversation (fake driver) ────────


async def _conversation(
    client: AsyncClient, headers: dict[str, str], guardrails: dict[str, Any]
) -> str:
    a = (await client.post("/api/v1/assistants", json={"name": "R"}, headers=headers)).json()
    cfg = a["draft_config"]
    cfg["guardrails"] = {**cfg["guardrails"], **guardrails}
    r = await client.put(f"/api/v1/assistants/{a['id']}/draft-config", json=cfg, headers=headers)
    assert r.status_code == 200, r.text
    r = await client.post(f"/api/v1/assistants/{a['id']}/conversations", json={}, headers=headers)
    return str(r.json()["id"])


async def _turn(cid: str, text: str) -> list[Any]:
    events: list[Any] = []
    await _run_turn(cid, text, events)
    return events


async def _run(cid: str) -> Run:
    async with get_sessionmaker()() as s:
        return (
            await s.scalars(
                select(Run)
                .where(Run.conversation_id == uuid.UUID(cid))
                .order_by(Run.created_at.desc())
            )
        ).first()  # type: ignore[return-value]


@pytest.fixture
def attempts(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every time the runtime starts the fake driver."""
    seen: list[str] = []
    real = FakeDriver.stream

    def counting(self: FakeDriver, **kwargs: Any) -> AsyncIterator[Any]:
        seen.append(kwargs["prompt"])
        return real(self, **kwargs)

    monkeypatch.setattr(FakeDriver, "stream", counting)
    return seen


@pytest.mark.anyio
async def test_a_runtime_that_died_before_saying_anything_is_started_again(
    client: AsyncClient, org_headers: dict[str, str], attempts: list[str]
) -> None:
    cid = await _conversation(client, org_headers, {})
    events = await _turn(cid, "fail: crash-once, then answer")
    assert len(attempts) == 2
    assert "error" not in [e.type for e in events]
    assert "you said:" in "".join(e.text for e in events if isinstance(e, TokenEvent))
    assert (await _run(cid)).status == "ok"


@pytest.mark.anyio
async def test_the_restart_happens_once_then_the_failure_is_reported(
    client: AsyncClient, org_headers: dict[str, str], attempts: list[str]
) -> None:
    cid = await _conversation(client, org_headers, {})
    events = await _turn(cid, "fail: crash")
    assert len(attempts) == settings.agent_turn_retries + 1
    (error,) = [e for e in events if e.type == "error"]
    assert (error.code, error.retryable) == ("agent_crashed", True)
    run = await _run(cid)
    assert (run.status, run.error) == ("error", error.message)


@pytest.mark.anyio
async def test_api_trouble_is_not_retried_again_by_the_platform(
    client: AsyncClient, org_headers: dict[str, str], attempts: list[str]
) -> None:
    """The CLI already retried it (CLAUDE_CODE_MAX_RETRIES)."""
    cid = await _conversation(client, org_headers, {})
    events = await _turn(cid, "fail: overloaded")
    assert len(attempts) == 1
    (error,) = [e for e in events if e.type == "error"]
    assert (error.code, error.retryable) == ("overloaded", True)
    events = await _turn(cid, "fail: auth")
    (error,) = [e for e in events if e.type == "error"]
    assert (error.code, error.retryable) == ("auth_failed", False)


@pytest.mark.anyio
async def test_with_no_retries_configured_the_first_failure_stands(
    client: AsyncClient,
    org_headers: dict[str, str],
    attempts: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "agent_turn_retries", 0)
    cid = await _conversation(client, org_headers, {})
    events = await _turn(cid, "fail: crash-once")
    assert len(attempts) == 1
    assert [e.code for e in events if e.type == "error"] == ["agent_crashed"]


@pytest.mark.anyio
async def test_a_turn_that_already_streamed_is_never_restarted(
    client: AsyncClient,
    org_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Restarting then would say (or do) things twice."""
    calls: list[int] = []

    class Flaky:
        name = "flaky"

        async def stream(self, **_kwargs: Any) -> AsyncIterator[Any]:
            calls.append(1)
            yield TokenEvent(text="Half an answer")
            yield ErrorEvent(code="agent_crashed", message="stopped", retryable=True)

    monkeypatch.setattr(runtime_mod, "get_driver", Flaky)
    cid = await _conversation(client, org_headers, {})
    events = await _turn(cid, "hello")
    assert calls == [1]
    assert [e.type for e in events if e.type in ("token", "error")] == ["token", "error"]


@pytest.mark.anyio
async def test_one_failure_reported_twice_is_shown_once(
    client: AsyncClient,
    org_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Twice:
        name = "twice"

        async def stream(self, **_kwargs: Any) -> AsyncIterator[Any]:
            yield TokenEvent(text="x")
            yield ErrorEvent(code="rate_limited", message="slow down", retryable=True)
            yield ErrorEvent(code="rate_limited", message="slow down", retryable=True)

    monkeypatch.setattr(runtime_mod, "get_driver", Twice)
    cid = await _conversation(client, org_headers, {})
    assert [e.code for e in await _turn(cid, "hello") if e.type == "error"] == ["rate_limited"]


@pytest.mark.anyio
async def test_a_refusal_is_recorded_as_one_not_as_an_error(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    cid = await _conversation(client, org_headers, {"refusal_fallback": False})
    events = await _turn(cid, "refuse: something")
    (notice,) = [e for e in events if e.type == "error"]
    assert (notice.code, notice.retryable) == ("refused", False)
    assert "declined" in notice.message and "fallback" not in notice.message
    run = await _run(cid)
    assert (run.status, run.stop_reason, run.fallback_model) == ("refused", "refusal", None)


@pytest.mark.anyio
async def test_with_the_fallback_on_the_other_model_answers(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    cid = await _conversation(client, org_headers, {"refusal_fallback": True})
    events = await _turn(cid, "refuse: something")
    assert "error" not in [e.type for e in events]
    answer = "".join(e.text for e in events if isinstance(e, TokenEvent))
    fallback = fallback_for(AssistantConfig().models.main.model)
    assert f"answering as {fallback}" in answer
    run = await _run(cid)
    assert (run.status, run.stop_reason, run.fallback_model) == ("ok", "end_turn", fallback)
    # And the API says so.
    detail = await client.get(f"/api/v1/conversations/{cid}/runs/{run.id}", headers=org_headers)
    assert detail.json()["fallback_model"] == fallback


@pytest.mark.anyio
async def test_a_driver_that_raises_is_reported_safely(
    client: AsyncClient,
    org_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The runtime's own catch-all, for a driver that raises instead of
    reporting: typed, and nothing of the exception reaches the client."""

    class Raises:
        name = "raises"

        async def stream(self, **_kwargs: Any) -> AsyncIterator[Any]:
            raise RuntimeError("failed reading /srv/app/secrets.env")
            yield  # pragma: no cover - makes this an async generator

    monkeypatch.setattr(runtime_mod, "get_driver", Raises)
    cid = await _conversation(client, org_headers, {})
    (error,) = [e for e in await _turn(cid, "hello") if e.type == "error"]
    assert (error.code, error.retryable) == ("agent_error", True)
    assert "/srv" not in error.message and "secrets" not in error.message
