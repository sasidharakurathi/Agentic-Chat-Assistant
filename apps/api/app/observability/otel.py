"""Optional OpenTelemetry wiring.

No-ops unless ``OTEL_EXPORTER_OTLP_ENDPOINT`` is set *and* the ``observability``
extra is installed (``pip install -e "apps/api[observability]"``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.config import settings
from app.logging import get_logger

if TYPE_CHECKING:
    from fastapi import FastAPI

log = get_logger(__name__)


def setup_tracing(app: FastAPI) -> None:
    if not settings.otel_exporter_otlp_endpoint:
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        log.warning(
            "otel_endpoint_set_but_packages_missing",
            hint='pip install -e "apps/api[observability]"',
        )
        return

    provider = TracerProvider(resource=Resource.create({"service.name": "assistant-studio-api"}))
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint))
    )
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app)
    log.info("otel_tracing_enabled", endpoint=settings.otel_exporter_otlp_endpoint)


__all__ = ["setup_tracing"]
