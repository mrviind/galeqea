"""OpenTelemetry tracing: optional, off unless configured.

Enable with ``GALEQEA_OTEL_ENABLED=true`` and point ``OTEL_EXPORTER_OTLP_ENDPOINT``
at a collector (e.g. ``http://otel-collector:4317``). Auto-instruments FastAPI,
SQLAlchemy and httpx. The dependencies live in the optional ``otel`` extra; if they
are not installed this is a no-op, so the default install carries no OTel weight.
"""

from __future__ import annotations

import logging

from ..config import settings

log = logging.getLogger("galeqea.otel")
_done = False


def enabled() -> bool:
    return bool(getattr(settings, "otel_enabled", False))


def setup(app) -> None:
    """Instrument the app for OTLP export. Idempotent; a no-op unless enabled and
    the OTel packages are installed."""
    global _done
    if _done or not enabled():
        return
    _done = True
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
        from opentelemetry.sdk.resources import SERVICE_NAME, Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        log.warning("GALEQEA_OTEL_ENABLED is set but the 'otel' extra is not "
                    "installed. Install apps/api[otel]. Tracing is off.")
        return

    provider = TracerProvider(resource=Resource.create({SERVICE_NAME: "galeqea"}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)

    FastAPIInstrumentor.instrument_app(app)
    HTTPXClientInstrumentor().instrument()
    try:
        from .db import engine
        SQLAlchemyInstrumentor().instrument(engine=engine)
    except Exception:  # noqa: BLE001 - DB instrumentation is best-effort
        pass
    log.info("OpenTelemetry tracing enabled (OTLP export).")
