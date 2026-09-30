"""The trace of one chat turn (task 1.9).

A turn is recorded as a span tree under the request that carried it:

    chat.turn                 the turn: prompt in, answer out, status
    ├── claude                a generation: model, tokens, cost
    └── tool:<name>           one per tool call, with its real start and latency

The attributes follow Langfuse's OpenTelemetry mapping (`langfuse.*`, and the
`gen_ai.*` semantic conventions it also reads), so Langfuse shows the turn as
a trace in the conversation's session with the model call priced. Any other
OTLP backend just sees well-labelled spans.

The children are written when the turn is finalized, with explicit start and
end times, not live. The turn is a generator that can be closed from another
task (a client disconnect), and an OTel context attached in one task cannot be
detached from another, so the turn span is never made "current". That costs
nothing: everything a child needs is on `TurnOutcome` by then.

Without the `observability` extra or a configured backend the spans are
no-ops, and the turn still gets a trace id (see `trace_ids`), still bound into
every log line it writes.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import TYPE_CHECKING, Any

from app.guardrails.pii import redact as redact_pii
from app.logging import get_logger
from app.models.conversation import RunStatus
from app.observability.otel import LLM_SCOPE
from app.observability.trace_ids import current_trace_id
from app.security.redact import strip_secrets

if TYPE_CHECKING:
    from app.agent.runtime import TurnOutcome

log = get_logger(__name__)

#: Longest prompt, answer or tool payload copied onto a span.
SPAN_TEXT_CHARS = 20_000


def _text(value: Any, pii: bool = False) -> str:
    """What a span may hold: no secrets ever, and no personal data when the
    assistant redacts it (`guardrails.pii_redaction`, task 5.3)."""
    raw = value if isinstance(value, str) else json.dumps(value, default=str)
    raw = strip_secrets(raw)
    if pii:
        raw = redact_pii(raw)[0]
    return raw[:SPAN_TEXT_CHARS]


def _tracer() -> Any | None:
    try:
        import opentelemetry.trace as trace  # noqa: PLR0402 - see otel.py
    except ImportError:
        return None
    return trace.get_tracer(LLM_SCOPE)


class TurnTrace:
    def __init__(
        self,
        span: Any | None,
        trace_id: str,
        *,
        model: str,
        started_ns: int,
        tracer: Any | None = None,
        conversation_id: uuid.UUID | None = None,
        pii_redaction: bool = False,
    ) -> None:
        self._conversation_id = conversation_id
        self._pii = pii_redaction
        self._span = span
        self._tracer = tracer
        self.trace_id = trace_id
        self._model = model
        self._started_ns = started_ns

    @classmethod
    def start(
        cls,
        *,
        conversation_id: uuid.UUID,
        assistant_id: uuid.UUID,
        org_id: uuid.UUID,
        user_ref: str | None,
        model: str,
        prompt: str,
        pii_redaction: bool = False,
    ) -> TurnTrace:
        started_ns = time.time_ns()
        tracer = _tracer()
        span = None
        if tracer is not None:
            attributes: dict[str, Any] = {
                "langfuse.trace.name": "chat turn",
                "langfuse.session.id": str(conversation_id),
                "langfuse.trace.input": _text(prompt, pii_redaction),
                "langfuse.observation.input": _text(prompt, pii_redaction),
                "langfuse.trace.metadata.assistant_id": str(assistant_id),
                "langfuse.trace.metadata.org_id": str(org_id),
                "assistant_studio.conversation_id": str(conversation_id),
                "assistant_studio.assistant_id": str(assistant_id),
                "assistant_studio.org_id": str(org_id),
            }
            if user_ref:
                attributes["langfuse.user.id"] = user_ref
            span = tracer.start_span("chat.turn", attributes=attributes, start_time=started_ns)
            ctx = span.get_span_context()
            if ctx.is_valid:
                trace_id = format(ctx.trace_id, "032x")
            else:  # no provider installed: a no-op span with no ids
                span = None
                trace_id = current_trace_id()
        else:
            trace_id = current_trace_id()
        log.info("turn_started", assistant_id=str(assistant_id), model=model)
        return cls(
            span,
            trace_id,
            model=model,
            started_ns=started_ns,
            tracer=tracer,
            conversation_id=conversation_id,
            pii_redaction=pii_redaction,
        )

    def finish(self, outcome: TurnOutcome, *, duration_ms: int, model_calls: int = 0) -> None:
        """End the turn's spans and log its result. Never raises: a tracing
        fault must not cost the user a turn that has already been saved."""
        # Explicit ids: on the normal path this runs after the turn's log
        # context has been unbound.
        log.info(
            "turn_finished",
            trace_id=self.trace_id,
            conversation_id=str(self._conversation_id),
            status=str(outcome.status),
            error=outcome.error,
            driver=outcome.driver_name,
            tokens_in=outcome.tokens_in,
            tokens_out=outcome.tokens_out,
            cost_usd=round(outcome.cost_usd, 6),
            duration_ms=duration_ms,
            tool_calls=len(outcome.tool_calls),
            model_calls=model_calls,
        )
        if self._span is None:
            return
        try:
            self._write_spans(outcome)
        except Exception:
            log.exception("turn_trace_failed")

    def _write_spans(self, outcome: TurnOutcome) -> None:
        import opentelemetry.trace as trace  # noqa: PLR0402 - see otel.py
        from opentelemetry.trace import Status, StatusCode

        turn_span, tracer = self._span, self._tracer
        assert turn_span is not None and tracer is not None
        parent = trace.set_span_in_context(turn_span)
        end_ns = time.time_ns()
        answer = _text(outcome.text.strip(), self._pii)

        generation = tracer.start_span(
            "claude",
            context=parent,
            start_time=self._started_ns,
            attributes={
                "langfuse.observation.type": "generation",
                "gen_ai.system": "anthropic",
                "gen_ai.request.model": self._model,
                "gen_ai.usage.input_tokens": outcome.tokens_in,
                "gen_ai.usage.output_tokens": outcome.tokens_out,
                "gen_ai.usage.cost": outcome.cost_usd,
                "langfuse.observation.output": answer,
                "assistant_studio.driver": outcome.driver_name,
                "assistant_studio.num_turns": outcome.num_turns,
            },
        )
        generation.end(end_time=end_ns)

        for call in outcome.tool_calls:
            call_id = str(call.get("id"))
            start = outcome.tool_started_ns.get(call_id, self._started_ns)
            latency = outcome.tool_latency_ms.get(call_id)
            span = tracer.start_span(
                f"tool:{call.get('name', '')}",
                context=parent,
                start_time=start,
                attributes={
                    "langfuse.observation.input": _text(call.get("input") or {}, self._pii),
                    "langfuse.observation.output": _text(call.get("output") or "", self._pii),
                    "assistant_studio.tool.name": str(call.get("name", "")),
                    "assistant_studio.tool.status": str(call.get("status") or "pending"),
                },
            )
            if call.get("status") == "error":
                span.set_attribute("langfuse.observation.level", "ERROR")
                span.set_status(Status(StatusCode.ERROR))
            span.end(end_time=start + latency * 1_000_000 if latency is not None else end_ns)

        turn_span.set_attribute("langfuse.trace.output", answer)
        turn_span.set_attribute("langfuse.observation.output", answer)
        turn_span.set_attribute("assistant_studio.run.status", str(outcome.status))
        if outcome.status != RunStatus.ok:
            level = "ERROR" if outcome.status == RunStatus.error else "WARNING"
            turn_span.set_attribute("langfuse.observation.level", level)
            turn_span.set_attribute("langfuse.observation.status_message", outcome.error or "")
        if outcome.status == RunStatus.error:
            turn_span.set_status(Status(StatusCode.ERROR, outcome.error or None))
        turn_span.end(end_time=end_ns)
        self._span = None


__all__ = ["SPAN_TEXT_CHARS", "TurnTrace"]
