"""Runs are readable, and every run carries a trace id (audit "never checked").

`runs.trace_id` was specified (plan §2.5) and never added, and the `runs`
table was write-only: recorded on every turn, read by nothing.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, ClassVar

import pytest
import structlog
from app.agent.events import TokenEvent, UsageEvent
from app.db.session import get_sessionmaker
from app.models.conversation import Run
from app.services import chat as chat_svc
from httpx import AsyncClient
from opentelemetry import trace
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags
from tests.test_chat import _new_assistant, _new_conversation, _send

pytestmark = pytest.mark.anyio

HEX32 = re.compile(r"^[0-9a-f]{32}$")


async def test_a_turn_records_a_trace_id_and_reports_it(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    aid = await _new_assistant(client, org_headers)
    cid = await _new_conversation(client, org_headers, aid)
    done = (await _send(client, org_headers, cid, "hello"))[-1]
    assert done["type"] == "done"
    assert HEX32.match(done["trace_id"]) and done["trace_id"] != "0" * 32

    async with get_sessionmaker()() as s:
        run = await s.get(Run, uuid.UUID(done["run_id"]))
        assert run is not None and run.trace_id == done["trace_id"]


async def test_an_active_otel_trace_is_the_one_recorded() -> None:
    """With tracing on, the run must carry the *request's* trace id, or it can
    never be found in the tracing backend. A remote span context is exactly
    what an incoming `traceparent` header produces."""
    from app.observability.trace_ids import current_trace_id

    ctx = SpanContext(
        trace_id=0x4BF92F3577B34DA6A3CE929D0E0E4736,
        span_id=0x00F067AA0BA902B7,
        is_remote=True,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
    )
    with trace.use_span(NonRecordingSpan(ctx)):
        assert current_trace_id() == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert current_trace_id() != "4bf92f3577b34da6a3ce929d0e0e4736"


class _ContextSpy:
    """A driver that records the logging context it runs under. The driver
    runs in its own task, so this proves the id reaches spawned tasks."""

    name = "spy"
    seen: ClassVar[dict[str, Any]] = {}

    async def stream(self, **_: Any) -> Any:
        _ContextSpy.seen = structlog.contextvars.get_contextvars()
        yield TokenEvent(text="ok")
        yield UsageEvent(tokens_in=1, tokens_out=1)


async def test_every_log_line_of_the_turn_carries_its_trace_id(
    client: AsyncClient, org_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.agent.runtime.get_driver", _ContextSpy)
    aid = await _new_assistant(client, org_headers)
    cid = uuid.UUID(await _new_conversation(client, org_headers, aid))
    async with get_sessionmaker()() as session:
        events = [e async for e in chat_svc.run_message(session, conversation_id=cid, text="x")]
    done = events[-1]
    assert _ContextSpy.seen.get("trace_id") == done.trace_id
    assert _ContextSpy.seen.get("conversation_id") == str(cid)
    assert "trace_id" not in structlog.contextvars.get_contextvars(), "unbound afterwards"


async def test_runs_are_listed_filtered_and_detailed(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    aid = await _new_assistant(client, org_headers, {"tools": {"calculator": {"enabled": True}}})
    cid = await _new_conversation(client, org_headers, aid)
    first = (await _send(client, org_headers, cid, "hello"))[-1]
    second = (await _send(client, org_headers, cid, "please calculate 2 + 2"))[-1]

    listed = (await client.get(f"/api/v1/conversations/{cid}/runs", headers=org_headers)).json()
    assert [r["id"] for r in listed["items"]] == [second["run_id"], first["run_id"]]

    only = (
        await client.get(
            f"/api/v1/conversations/{cid}/runs",
            params={"message_id": second["message_id"]},
            headers=org_headers,
        )
    ).json()["items"]
    assert [r["id"] for r in only] == [second["run_id"]]

    detail = (
        await client.get(
            f"/api/v1/conversations/{cid}/runs/{second['run_id']}", headers=org_headers
        )
    ).json()
    assert detail["trace_id"] == second["trace_id"]
    assert [s["name"] for s in detail["steps"]] == ["mcp__caps__calculator"]
    assert detail["steps"][0]["status"] == "success"


async def test_a_run_is_only_reachable_through_its_own_conversation(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    aid = await _new_assistant(client, org_headers)
    a = await _new_conversation(client, org_headers, aid)
    b = await _new_conversation(client, org_headers, aid)
    run_id = (await _send(client, org_headers, a, "hello"))[-1]["run_id"]
    r = await client.get(f"/api/v1/conversations/{b}/runs/{run_id}", headers=org_headers)
    assert r.status_code == 404
