"""The trace id a chat turn is recorded under.

Every run gets one, tracing or not. With OpenTelemetry enabled it is the id of
the active trace, so `runs.trace_id` finds the turn in the tracing backend.
Without it, a fresh id in the same W3C format (32 lowercase hex, never all
zeros) is minted, and is still useful: it is bound into every log line the
turn writes, so a run row leads straight to its logs.
"""

from __future__ import annotations

import secrets


def current_trace_id() -> str:
    try:
        # Aliased submodule import: opentelemetry is a namespace package (see
        # otel.py). Absent unless the `observability` extra is installed.
        import opentelemetry.trace as trace  # noqa: PLR0402
    except ImportError:
        return secrets.token_hex(16)
    ctx = trace.get_current_span().get_span_context()
    if ctx.is_valid:
        return format(ctx.trace_id, "032x")
    return secrets.token_hex(16)


__all__ = ["current_trace_id"]
