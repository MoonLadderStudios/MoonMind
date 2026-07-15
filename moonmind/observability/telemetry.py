"""Process-wide OpenTelemetry bootstrap and bounded semantic helpers.

Telemetry is deliberately best-effort: exporters and instrumentation never own
application correctness, and workflow code never calls this module.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import quote, urlparse

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider

logger = logging.getLogger(__name__)
_lock = threading.Lock()
_state: dict[str, TracerProvider | None] = {"provider": None}
_SECRET_KEY = re.compile(r"(?i)(authorization|cookie|password|secret|token|api[-_]?key)")


@dataclass(frozen=True, slots=True)
class TelemetrySettings:
    enabled: bool = False
    service_name: str = "moonmind"
    service_version: str = "0.1.0"
    environment: str = "local"
    instance_id: str | None = None
    worker_fleet: str | None = None
    otlp_endpoint: str | None = None
    otlp_protocol: str = "grpc"
    sample_ratio: float = 1.0
    trace_url_template: str | None = None
    logs_url_template: str | None = None

    @classmethod
    def from_env(cls, *, service_name: str | None = None) -> "TelemetrySettings":
        ratio = float(os.getenv("MOONMIND_OTEL_SAMPLE_RATIO", "1"))
        if not 0 <= ratio <= 1:
            raise ValueError("MOONMIND_OTEL_SAMPLE_RATIO must be between 0 and 1")
        protocol = os.getenv("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc").strip().lower()
        if protocol not in {"grpc", "http/protobuf"}:
            raise ValueError("OTEL_EXPORTER_OTLP_PROTOCOL must be grpc or http/protobuf")
        return cls(
            enabled=os.getenv("MOONMIND_ENABLE_OPENTELEMETRY", "0") == "1",
            service_name=service_name or os.getenv("OTEL_SERVICE_NAME", "moonmind"),
            service_version=os.getenv("MOONMIND_VERSION", "0.1.0"),
            environment=os.getenv("MOONMIND_ENVIRONMENT", "local"),
            instance_id=os.getenv("MOONMIND_INSTANCE_ID") or None,
            worker_fleet=os.getenv("TEMPORAL_WORKER_FLEET") or None,
            otlp_endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or None,
            otlp_protocol=protocol,
            sample_ratio=ratio,
            trace_url_template=_validated_template(os.getenv("MOONMIND_TRACE_URL_TEMPLATE")),
            logs_url_template=_validated_template(os.getenv("MOONMIND_LOGS_URL_TEMPLATE")),
        )


def _validated_template(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("telemetry link templates must be absolute HTTP(S) URLs")
    if parsed.username or parsed.password:
        raise ValueError("telemetry link templates must not contain credentials")
    return value


def sanitize_attributes(values: Mapping[str, object]) -> dict[str, str | int | float | bool]:
    """Return bounded, scalar span attributes with secret-like fields removed."""
    clean: dict[str, str | int | float | bool] = {}
    for key, value in values.items():
        if _SECRET_KEY.search(key) or value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            clean[key] = value[:256] if isinstance(value, str) else value
    return clean


def build_backend_url(template: str | None, **identifiers: str | None) -> str | None:
    if not template:
        return None
    safe = {key: quote(value or "", safe="") for key, value in identifiers.items()}
    try:
        return template.format_map(safe)
    except (KeyError, ValueError):
        return None


def initialize_telemetry(settings: TelemetrySettings) -> TracerProvider | None:
    """Initialize one process provider and OTLP exporter, failing open."""
    if not settings.enabled:
        return None
    with _lock:
        if _state["provider"] is not None:
            return _state["provider"]
        try:
            attributes = {
                "service.name": settings.service_name,
                "service.version": settings.service_version,
                "deployment.environment.name": settings.environment,
            }
            if settings.instance_id:
                attributes["service.instance.id"] = settings.instance_id
            if settings.worker_fleet:
                attributes["moonmind.worker.fleet"] = settings.worker_fleet
            provider = TracerProvider(resource=Resource.create(attributes))
            if settings.otlp_endpoint:
                from opentelemetry.sdk.trace.export import BatchSpanProcessor
                if settings.otlp_protocol == "http/protobuf":
                    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
                else:
                    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
                provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otlp_endpoint)))
            trace.set_tracer_provider(provider)
            _state["provider"] = provider
            return provider
        except Exception:
            logger.warning("OpenTelemetry initialization failed; continuing without export", exc_info=True)
            return None


def temporal_tracing_interceptors() -> list[object]:
    if not TelemetrySettings.from_env().enabled:
        return []
    try:
        from temporalio.contrib.opentelemetry import TracingInterceptor
        return [TracingInterceptor()]
    except Exception:
        logger.warning("Temporal tracing interceptor unavailable", exc_info=True)
        return []


def instrument_fastapi(app: object) -> None:
    if not TelemetrySettings.from_env(service_name="moonmind-api").enabled:
        return
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        FastAPIInstrumentor.instrument_app(app, excluded_urls="/healthz,/readyz,/metrics")
    except Exception:
        logger.warning("FastAPI instrumentation unavailable", exc_info=True)
