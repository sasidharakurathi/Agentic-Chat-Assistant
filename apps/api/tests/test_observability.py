"""Traces and logs (task 1.9).

- A chat turn is a span tree: the turn, its model call (priced) and one span
  per tool call, under the id stored on the run.
- Langfuse receives only those spans; an OTLP collector receives everything.
- Log lines never carry a credential, by field name or by shape.
- The worker configures logging and tracing like the API does.
"""

from __future__ import annotations

import base64
import uuid
from typing import Any

import pytest
import structlog
from app.agent.events import ErrorEvent, TokenEvent, UsageEvent
from app.db.session import get_sessionmaker
from app.models.conversation import Run
from app.observability import otel, turn_trace
from app.security.redact import REDACTED, redact_event
from app.services import chat as chat_svc
from httpx import AsyncClient
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode
from sqlalchemy import select
from tests.test_chat import _new_assistant, _new_conversation, _send

pytestmark = pytest.mark.anyio


@pytest.fixture
def spans(monkeypatch: pytest.MonkeyPatch) -> InMemorySpanExporter:
    """Turn spans go to memory. A local provider, not the global one: the
    global provider can be set once per process."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(turn_trace, "_tracer", lambda: provider.get_tracer(otel.LLM_SCOPE))
    return exporter


def _by_name(exporter: InMemorySpanExporter) -> dict[str, ReadableSpan]:
    return {s.name: s for s in exporter.get_finished_spans()}


async def test_a_turn_is_a_span_tree_under_the_runs_trace_id(
    client: AsyncClient, org_headers: dict[str, str], spans: InMemorySpanExporter
) -> None:
    aid = await _new_assistant(client, org_headers, {"tools": {"calculator": {"enabled": True}}})
    cid = await _new_conversation(client, org_headers, aid)
    await _send(client, org_headers, cid, "please calculate 21 + 21")

    got = _by_name(spans)
    turn, generation = got["chat.turn"], got["claude"]
    tool = got["tool:mcp__caps__calculator"]
    async with get_sessionmaker()() as s:
        run = await s.scalar(select(Run).where(Run.conversation_id == uuid.UUID(cid)))
    assert run is not None
    assert format(turn.context.trace_id, "032x") == run.trace_id

    assert turn.attributes["langfuse.session.id"] == cid
    assert turn.attributes["langfuse.trace.input"] == "please calculate 21 + 21"
    assert "42" in str(turn.attributes["langfuse.trace.output"])
    for child in (generation, tool):
        assert child.parent is not None and child.parent.span_id == turn.context.span_id

    assert generation.attributes["langfuse.observation.type"] == "generation"
    assert generation.attributes["gen_ai.request.model"] == run.model
    assert generation.attributes["gen_ai.usage.input_tokens"] == run.tokens_in
    assert tool.attributes["assistant_studio.tool.status"] == "success"
    assert "42" in str(tool.attributes["langfuse.observation.output"])
    assert tool.start_time is not None and turn.start_time is not None
    assert tool.start_time >= turn.start_time


class _Failing:
    name = "failing"

    async def stream(self, **_: Any) -> Any:
        yield TokenEvent(text="partial")
        yield ErrorEvent(code="agent_error", message="upstream fell over")
        yield UsageEvent(tokens_in=1, tokens_out=1)


async def test_a_failed_turn_is_marked_as_an_error(
    client: AsyncClient,
    org_headers: dict[str, str],
    spans: InMemorySpanExporter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.agent.runtime.get_driver", _Failing)
    aid = await _new_assistant(client, org_headers)
    cid = uuid.UUID(await _new_conversation(client, org_headers, aid))
    async with get_sessionmaker()() as session:
        async for _ in chat_svc.run_message(session, conversation_id=cid, text="q"):
            pass

    turn = _by_name(spans)["chat.turn"]
    assert turn.status.status_code == StatusCode.ERROR
    assert turn.attributes["langfuse.observation.level"] == "ERROR"
    assert turn.attributes["langfuse.observation.status_message"] == "upstream fell over"


async def test_secrets_never_reach_a_span(
    client: AsyncClient, org_headers: dict[str, str], spans: InMemorySpanExporter
) -> None:
    key = "sk-ant-api03-" + "Z" * 40
    aid = await _new_assistant(client, org_headers)
    cid = await _new_conversation(client, org_headers, aid)
    await _send(client, org_headers, cid, f"my key is {key}")

    for span in spans.get_finished_spans():
        assert key not in str(dict(span.attributes or {}))


def test_langfuse_gets_only_llm_spans_and_the_collector_gets_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exporters: dict[str, InMemorySpanExporter] = {}

    def fake_exporter(*, endpoint: str, headers: dict[str, str] | None = None) -> Any:
        exporters[endpoint] = InMemorySpanExporter()
        return exporters[endpoint]

    monkeypatch.setattr(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter", fake_exporter
    )
    monkeypatch.setattr(otel.settings, "otel_exporter_otlp_endpoint", "http://collector:4318")
    monkeypatch.setattr(otel.settings, "langfuse_host", "http://langfuse:3000/")
    monkeypatch.setattr(otel.settings, "langfuse_public_key", "pk-test")
    monkeypatch.setattr(otel.settings, "langfuse_secret_key", "sk-test")

    from opentelemetry.trace import set_span_in_context

    provider = otel._build_provider("test")
    http = provider.get_tracer("opentelemetry.instrumentation.fastapi").start_span("POST")
    turn = provider.get_tracer(otel.LLM_SCOPE).start_span(
        "chat.turn", context=set_span_in_context(http)
    )
    provider.get_tracer(otel.LLM_SCOPE).start_span(
        "claude", context=set_span_in_context(turn)
    ).end()
    provider.get_tracer("opentelemetry.instrumentation.sqlalchemy").start_span("SELECT").end()
    turn.end()
    http.end()
    provider.force_flush()

    collector = exporters["http://collector:4318/v1/traces"]
    langfuse = {s.name: s for s in exporters[otel.langfuse_endpoint()].get_finished_spans()}
    assert sorted(s.name for s in collector.get_finished_spans()) == [
        "POST",
        "SELECT",
        "chat.turn",
        "claude",
    ]
    assert sorted(langfuse) == ["chat.turn", "claude"]
    # The request span never reaches Langfuse, so the turn is its root there,
    # while its own children keep their parent.
    assert langfuse["chat.turn"].parent is None
    assert langfuse["claude"].parent is not None
    assert langfuse["claude"].parent.span_id == turn.get_span_context().span_id
    (collected_turn,) = [s for s in collector.get_finished_spans() if s.name == "chat.turn"]
    assert collected_turn.parent is not None, "the collector keeps the full tree"


def test_langfuse_auth_is_basic_with_the_key_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(otel.settings, "langfuse_public_key", "pk-lf-1")
    monkeypatch.setattr(otel.settings, "langfuse_secret_key", "sk-lf-2")
    header = otel.langfuse_headers()["Authorization"]
    assert base64.b64decode(header.removeprefix("Basic ")) == b"pk-lf-1:sk-lf-2"


@pytest.mark.parametrize(
    ("base", "expected"),
    [
        ("http://c:4318", "http://c:4318/v1/traces"),
        ("http://c:4318/", "http://c:4318/v1/traces"),
        ("http://c:4318/v1/traces", "http://c:4318/v1/traces"),
    ],
)
def test_the_collector_endpoint_gets_the_signal_path(base: str, expected: str) -> None:
    assert otel.otlp_traces_endpoint(base) == expected


def test_nothing_is_set_up_without_a_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(otel.settings, "otel_exporter_otlp_endpoint", "")
    monkeypatch.setattr(otel.settings, "langfuse_host", "")
    assert otel.setup_tracing() is False


def test_log_fields_named_like_credentials_are_replaced() -> None:
    out = redact_event(
        None,
        "info",
        {
            "event": "connect",
            "password": "hunter2",
            "api_key": "abc",
            "headers": {"Authorization": "Basic Zm9vOmJhcg=="},
            "tokens_in": 12,
            "refresh_token": "r",
        },
    )
    assert out["password"] == out["api_key"] == out["refresh_token"] == REDACTED
    assert out["headers"]["Authorization"] == REDACTED
    assert out["tokens_in"] == 12, "a count named like a token is still a count"


def test_credential_shapes_in_log_text_are_masked() -> None:
    key = "sk-ant-api03-" + "Q" * 40
    out = redact_event(
        None, "error", {"event": f"call failed with {key}", "dsn": None, "rows": [f"x {key}"]}
    )
    assert key not in str(out) and REDACTED in out["event"]
    assert out["dsn"] is None


def test_the_redaction_processor_is_installed() -> None:
    from app.logging import configure_logging

    configure_logging()
    assert redact_event in structlog.get_config()["processors"]


async def test_the_worker_sets_up_logging_and_tracing(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import worker

    calls: list[str] = []
    monkeypatch.setattr(worker, "configure_logging", lambda *_a: calls.append("logging"))
    monkeypatch.setattr(
        worker, "setup_tracing", lambda **k: calls.append(f"tracing:{k['service']}")
    )
    await worker.WorkerSettings.on_startup({})
    assert calls == ["logging", "tracing:assistant-studio-worker"]


async def test_a_disconnected_turn_still_ends_its_span(
    client: AsyncClient,
    org_headers: dict[str, str],
    spans: InMemorySpanExporter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The client going away mid-turn is the path a span is most likely to be
    left open on, and an open span is never exported."""
    import asyncio

    from tests.test_turn_concurrency import _Gate

    gate = _Gate()
    monkeypatch.setattr("app.agent.runtime.get_driver", lambda: gate)
    aid = await _new_assistant(client, org_headers)
    cid = uuid.UUID(await _new_conversation(client, org_headers, aid))
    async with get_sessionmaker()() as session:
        stream = chat_svc.run_message(session, conversation_id=cid, text="x")
        waiting = asyncio.create_task(stream.__anext__())
        await asyncio.wait_for(gate.started.wait(), 5)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        await stream.aclose()

    turn = _by_name(spans)["chat.turn"]
    assert turn.attributes["assistant_studio.run.status"] == "aborted"
    assert turn.attributes["langfuse.observation.level"] == "WARNING"
