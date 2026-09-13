"""Optional OpenTelemetry and Sentry bootstrap with no-op degradation."""

from __future__ import annotations

from contextlib import contextmanager
import logging
import os
from threading import Lock
from typing import Any, Iterator, Mapping

logger = logging.getLogger(__name__)
_LOCK = Lock()
_INITIALIZED = False


def initialize_observability(service_name: str = "artpm-agent") -> bool:
    """Initialize configured exporters exactly once per process."""
    global _INITIALIZED
    with _LOCK:
        if _INITIALIZED:
            return True
        sentry_dsn = os.getenv("SENTRY_DSN", "").strip()
        if sentry_dsn:
            try:
                import sentry_sdk

                sentry_sdk.init(
                    dsn=sentry_dsn,
                    environment=os.getenv("ARTPM_ENV", "development"),
                    release=os.getenv("ARTPM_RELEASE") or None,
                    traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1")),
                    send_default_pii=False,
                )
            except Exception as error:
                logger.warning("Sentry initialization failed: %s", error)

        endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
        if endpoint:
            try:
                from opentelemetry import trace
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
                from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
                from opentelemetry import metrics
                from opentelemetry.sdk.resources import Resource
                from opentelemetry.sdk.metrics import MeterProvider
                from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
                from opentelemetry.sdk.trace import TracerProvider
                from opentelemetry.sdk.trace.export import BatchSpanProcessor

                provider = TracerProvider(
                    resource=Resource.create({"service.name": service_name})
                )
                trace_endpoint = os.getenv(
                    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
                    f"{endpoint.rstrip('/')}/v1/traces",
                )
                provider.add_span_processor(
                    BatchSpanProcessor(OTLPSpanExporter(endpoint=trace_endpoint))
                )
                trace.set_tracer_provider(provider)
                metric_endpoint = os.getenv(
                    "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
                    f"{endpoint.rstrip('/')}/v1/metrics",
                )
                metrics.set_meter_provider(
                    MeterProvider(
                        resource=Resource.create({"service.name": service_name}),
                        metric_readers=[
                            PeriodicExportingMetricReader(
                                OTLPMetricExporter(endpoint=metric_endpoint)
                            )
                        ],
                    )
                )
            except Exception as error:
                logger.warning("OpenTelemetry initialization failed: %s", error)
                return False
        _INITIALIZED = True
        return True


def instrument_fastapi(app: Any) -> bool:
    """Attach FastAPI tracing when the optional instrumentation is installed."""
    initialize_observability("artpm-api")
    if not os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return False
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)
        return True
    except Exception as error:
        logger.warning("FastAPI instrumentation failed: %s", error)
        return False


@contextmanager
def traced(
    name: str,
    *,
    attributes: Mapping[str, Any] | None = None,
) -> Iterator[Any]:
    """Create a span when OTel is installed, otherwise behave as a no-op."""
    initialize_observability()
    try:
        from opentelemetry import trace

        tracer = trace.get_tracer("artpm-agent")
        with tracer.start_as_current_span(name, attributes=dict(attributes or {})) as span:
            yield span
    except ImportError:
        yield None
    except Exception as error:
        capture_exception(error)
        raise


def capture_exception(error: BaseException) -> None:
    if not os.getenv("SENTRY_DSN"):
        return
    try:
        import sentry_sdk

        sentry_sdk.capture_exception(error)
    except Exception:
        logger.debug("Sentry exception capture failed", exc_info=True)


__all__ = [
    "capture_exception",
    "initialize_observability",
    "instrument_fastapi",
    "traced",
]
