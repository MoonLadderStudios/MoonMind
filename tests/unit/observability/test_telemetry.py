import pytest

from moonmind.observability import telemetry
from moonmind.observability.telemetry import (
    TelemetrySettings,
    build_backend_url,
    sanitize_attributes,
)


def test_settings_reject_invalid_sampling(monkeypatch):
    monkeypatch.setenv("MOONMIND_OTEL_SAMPLE_RATIO", "2")
    with pytest.raises(ValueError, match="between 0 and 1"):
        TelemetrySettings.from_env()


def test_sanitize_attributes_removes_secrets_and_bounds_text():
    result = sanitize_attributes({"token": "nope", "prompt": "x" * 300, "retry": 2})
    assert result == {"prompt": "x" * 256, "retry": 2}


def test_backend_links_require_safe_absolute_template(monkeypatch):
    monkeypatch.setenv("MOONMIND_TRACE_URL_TEMPLATE", "javascript:{trace_id}")
    with pytest.raises(ValueError, match="absolute HTTP"):
        TelemetrySettings.from_env()
    assert build_backend_url("https://traces.example/t/{trace_id}", trace_id="a/b") == "https://traces.example/t/a%2Fb"


def test_initialize_is_idempotent(monkeypatch):
    telemetry._state["trace_provider"] = None
    telemetry._state["meter_provider"] = None
    installed_traces = []
    installed_meters = []
    monkeypatch.setattr(telemetry.trace, "set_tracer_provider", installed_traces.append)
    monkeypatch.setattr(telemetry.metrics, "set_meter_provider", installed_meters.append)
    settings = TelemetrySettings(enabled=True)

    first = telemetry.initialize_telemetry(settings)
    second = telemetry.initialize_telemetry(settings)

    assert first is second
    assert installed_traces == [first]
    assert len(installed_meters) == 1
    assert telemetry._state["meter_provider"] is installed_meters[0]
    telemetry._state["trace_provider"] = None
    telemetry._state["meter_provider"] = None


@pytest.mark.parametrize("convention", ["", "http", "http/dup"])
@pytest.mark.parametrize(("transport", "route"), [
    ("websocket", "/ws/v1/terminal/session-1"),
    ("websocket", "/api/v1/oauth-sessions/session-1/terminal/ws"),
    ("http", "/ordinary-query"),
])
def test_instrumented_request_exports_no_query_credentials(monkeypatch, convention, transport, route):
    from fastapi import FastAPI, Request, WebSocket
    from fastapi.testclient import TestClient
    from opentelemetry import trace
    from opentelemetry.instrumentation._semconv import _OpenTelemetrySemanticConventionStability
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.sampling import ALWAYS_ON
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    monkeypatch.setenv("MOONMIND_ENABLE_OPENTELEMETRY", "1")
    monkeypatch.setenv("OTEL_SEMCONV_STABILITY_OPT_IN", convention)
    monkeypatch.setattr(_OpenTelemetrySemanticConventionStability, "_initialized", False)
    monkeypatch.setattr(_OpenTelemetrySemanticConventionStability, "_OTEL_SEMCONV_STABILITY_SIGNAL_MAPPING", {})
    exporter = InMemorySpanExporter()
    provider = TracerProvider(sampler=ALWAYS_ON)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(trace, "_TRACER_PROVIDER", provider)
    app = FastAPI()
    received = []

    @app.websocket(route)
    async def websocket_handler(websocket: WebSocket):
        received.append(dict(websocket.query_params))
        await websocket.accept()
        await websocket.send_text("ok")
        await websocket.close()

    @app.get(route)
    async def http_handler(request: Request):
        received.append(dict(request.query_params))
        return {"ok": True}

    telemetry.instrument_fastapi(app)
    try:
        with TestClient(app) as client:
            target = route + "?token=synthetic-private-token&ordinary=value"
            if transport == "websocket":
                with client.websocket_connect(target) as websocket:
                    assert websocket.receive_text() == "ok"
            else:
                assert client.get(target).json() == {"ok": True}
        assert received == [{"token": "synthetic-private-token", "ordinary": "value"}]
        spans = exporter.get_finished_spans()
        assert spans, "The request must remain observable"
        serialized = repr([(span.name, dict(span.attributes or {})) for span in spans])
        assert "synthetic-private-token" not in serialized
        assert "ordinary=value" not in serialized
        assert route in serialized
    finally:
        FastAPIInstrumentor.uninstrument_app(app)
        provider.shutdown()
