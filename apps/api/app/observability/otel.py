"""OpenTelemetry and Langfuse wiring (task 1.9).

Two destinations, each optional, both fed from one tracer provider:

- **An OTLP collector** (`OTEL_EXPORTER_OTLP_ENDPOINT`): every span: HTTP
  requests, SQL statements, chat turns, worker jobs.
- **Langfuse** (`LANGFUSE_HOST` + keys): only the LLM spans (the
  `assistant_studio.llm` scope, see `turn_trace.py`), sent to its OTLP
  endpoint. Langfuse is for reading conversations and their cost; a page of
  `SELECT`s under every turn would bury them.

Nothing happens unless one of them is configured *and* the `observability`
extra is installed (`pip install -e "apps/api[observability]"`; the Docker
image has it).
"""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING, Any

from app.config import settings
from app.logging import get_logger

if TYPE_CHECKING:
    from fastapi import FastAPI

log = get_logger(__name__)

#: The instrumentation scope of the spans Langfuse receives.
LLM_SCOPE = "assistant_studio.llm"

_state: dict[str, Any] = {"provider": None, "sqlalchemy": False}


def langfuse_endpoint() -> str:
    return settings.langfuse_host.rstrip("/") + "/api/public/otel/v1/traces"


def langfuse_headers() -> dict[str, str]:
    """Basic auth with the project's key pair, as Langfuse's OTLP endpoint wants."""
    pair = f"{settings.langfuse_public_key}:{settings.langfuse_secret_key}"
    return {"Authorization": "Basic " + base64.b64encode(pair.encode()).decode()}


def otlp_traces_endpoint(base: str) -> str:
    """`OTEL_EXPORTER_OTLP_ENDPOINT` is a base URL by the OTel spec: the SDK
    appends `/v1/traces` when it reads the variable itself, but not when the
    URL is passed to the exporter, as here. Passing the base as-is (as this
    module used to) posted spans to the collector's root, which 404s."""
    base = base.rstrip("/")
    return base if base.endswith("/v1/traces") else base + "/v1/traces"


def langfuse_enabled() -> bool:
    return bool(
        settings.langfuse_host and settings.langfuse_public_key and settings.langfuse_secret_key
    )


def tracing_configured() -> bool:
    return bool(settings.otel_exporter_otlp_endpoint) or langfuse_enabled()


def _build_provider(service: str) -> Any:
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    class _ScopeFilter(BatchSpanProcessor):
        """A batch processor that only takes spans from one instrumentation
        scope, and makes each of them a root unless its parent is in that
        scope too. The turn span's parent is the HTTP request span, which this
        exporter never sends; a parent the backend never receives leaves the
        turn hanging off nothing in Langfuse's tree."""

        def __init__(self, exporter: Any, scope: str) -> None:
            super().__init__(exporter)
            self._scope = scope
            self._open: set[int] = set()

        def _ours(self, span: ReadableSpan) -> bool:
            scope = span.instrumentation_scope
            return scope is not None and scope.name == self._scope

        def on_start(self, span: Any, parent_context: Any = None) -> None:
            if self._ours(span):
                self._open.add(span.context.span_id)

        def on_end(self, span: ReadableSpan) -> None:
            if not self._ours(span):
                return
            if span.parent is not None and span.parent.span_id not in self._open:
                span = ReadableSpan(
                    name=span.name,
                    context=span.context,
                    parent=None,
                    resource=span.resource,
                    attributes=span.attributes,
                    events=span.events,
                    links=span.links,
                    kind=span.kind,
                    status=span.status,
                    start_time=span.start_time,
                    end_time=span.end_time,
                    instrumentation_scope=span.instrumentation_scope,
                )
            if span.context is not None:
                self._open.discard(span.context.span_id)
            super().on_end(span)

    provider = TracerProvider(
        resource=Resource.create(
            {"service.name": service, "deployment.environment": settings.app_env}
        )
    )
    if settings.otel_exporter_otlp_endpoint:
        endpoint = otlp_traces_endpoint(settings.otel_exporter_otlp_endpoint)
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
        log.info("otel_tracing_enabled", endpoint=endpoint)
    if langfuse_enabled():
        exporter = OTLPSpanExporter(endpoint=langfuse_endpoint(), headers=langfuse_headers())
        provider.add_span_processor(_ScopeFilter(exporter, LLM_SCOPE))
        log.info("langfuse_tracing_enabled", host=settings.langfuse_host)
    return provider


def setup_tracing(app: FastAPI | None = None, *, service: str = "assistant-studio-api") -> bool:
    """Install the tracer provider (once per process) and instrument the app
    and the SQLAlchemy engine. Returns whether tracing is on."""
    if not tracing_configured():
        return False
    try:
        # Aliased submodule import, not `from opentelemetry import trace`:
        # opentelemetry is a namespace package, and mypy won't resolve an
        # attribute-style import from one. (ruff PLR0402 prefers the other
        # form; mypy wins here because it actually fails the build.)
        import opentelemetry.trace as trace  # noqa: PLR0402
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
    except ImportError:
        log.warning(
            "tracing_configured_but_packages_missing",
            hint='pip install -e "apps/api[observability]"',
        )
        return False

    if _state["provider"] is None:
        provider = _build_provider(service)
        trace.set_tracer_provider(provider)
        _state["provider"] = provider
    if app is not None:
        FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz,readyz")
    if not _state["sqlalchemy"]:
        from app.db.session import get_engine

        SQLAlchemyInstrumentor().instrument(engine=get_engine().sync_engine)
        _state["sqlalchemy"] = True
    return True


def shutdown_tracing() -> None:
    """Flush what is still batched. A worker or API that exits without this
    loses the last few seconds of spans, which are usually the interesting ones."""
    provider = _state["provider"]
    if provider is not None:
        provider.shutdown()
        _state["provider"] = None


__all__ = [
    "LLM_SCOPE",
    "langfuse_enabled",
    "langfuse_endpoint",
    "langfuse_headers",
    "otlp_traces_endpoint",
    "setup_tracing",
    "shutdown_tracing",
    "tracing_configured",
]
