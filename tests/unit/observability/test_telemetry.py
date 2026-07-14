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
    telemetry._state["provider"] = None
    installed = []
    monkeypatch.setattr(telemetry.trace, "set_tracer_provider", installed.append)
    settings = TelemetrySettings(enabled=True)

    first = telemetry.initialize_telemetry(settings)
    second = telemetry.initialize_telemetry(settings)

    assert first is second
    assert installed == [first]
    telemetry._state["provider"] = None
